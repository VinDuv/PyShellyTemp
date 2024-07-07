"""
Web framework tests
"""

from http import HTTPStatus
from unittest.mock import patch
from urllib.parse import urlencode
import dataclasses
import io
import pathlib
import socket
import tempfile
import unittest

from pyshellytemp.web import redirect, redirect_to_view, route, view_path
from pyshellytemp.web import HTTPRequest, HTTPTextResponse, HTTPFileResponse


@patch('pyshellytemp.web.routing.configure_logging', lambda: None)
class WebTest(unittest.TestCase):
    def test_no_views(self):
        with self.assertRaisesRegex(AssertionError, "No views are loaded\."):
            route.get_wsgi_app()

    def test_basic_get(self):
        test_case = self

        # Use a class method since standard routes are tested elsewhere
        @route.to_class_method('/')
        class SomeRouteClass:
            @classmethod
            def handle_request(cls, request):
                test_case.assertIsInstance(request, HTTPRequest)
                return HTTPTextResponse("Test.")

        self.assertEqual(do_req('/').get_html(), "Test.")

        err_html = do_req('/nonexistent').get_html("404 Not Found")
        self.assertIn("Not Found", err_html)

        err_html = do_req('/nonexistent/').get_html("404 Not Found")
        self.assertIn("Not Found", err_html)

    def test_form_post(self):
        form_data = ...

        @route('/')
        def request(request):
            nonlocal form_data
            form_data = None
            form_data = request.post.get_form_data()
            return HTTPTextResponse("OK")

        env = {
            'REQUEST_METHOD': 'XXX',
            'wsgi.input': NotImplemented,
        }

        do_req('/', env).check_msg("405 Method Not Allowed",
            "Unknown method 'XXX'")

        env['REQUEST_METHOD'] = 'POST'
        do_req('/', env).check_msg("400 Bad Request",
            "Bad POST request headers")

        env['CONTENT_TYPE'] = 'blah'
        env['CONTENT_LENGTH'] = 'blah'

        do_req('/', env).check_msg("400 Bad Request",
            "Bad POST request headers")

        self.assertIs(form_data, ...)

        env['CONTENT_LENGTH'] = str(1024 * 1024)
        do_req('/', env).check_msg("400 Bad Request",
            "Unexpected form type")
        self.assertIs(form_data, None)

        env['CONTENT_TYPE'] = 'application/x-www-form-urlencoded'
        do_req('/', env).check_msg("413 Request Entity Too Large",
            "Form too large")

        env['CONTENT_LENGTH'] = str(42)
        env['wsgi.input'] = io.BytesIO(b'abc')
        do_req('/', env).check_msg("400 Bad Request",
            "Truncated request content")

        env['wsgi.input'] = io.BytesIO(b'\xff' * 42)
        do_req('/', env).check_msg("400 Bad Request", "Bad form data")

        env['CONTENT_LENGTH'] = str(3)
        env['wsgi.input'] = io.BytesIO(b'a=\xff')
        do_req('/', env).check_msg("400 Bad Request", "Bad form data")

        encoded_dict = {
            'é': 'à=<#?',
            'x': '',
            'b': '123',
        }

        data = io.BytesIO(urlencode(encoded_dict).encode('ascii'))
        env['wsgi.input'] = data
        env['CONTENT_LENGTH'] = str(len(data.getvalue()))
        do_req('/', env).check_msg("200 OK", "OK")

    def test_cookie(self):
        cookie_data = ...

        @route('/')
        def request(request):
            nonlocal cookie_data
            cookie_data = None
            cookie_data = request.get_cookies()
            return HTTPTextResponse("OK")

        env = {
            'HTTP_COOKIE': 'x="a=b";y=z',
        }

        do_req('/', env).check_msg("200 OK", "OK")
        self.assertEqual(cookie_data, {'x': 'a=b', 'y':'z'})

    def test_stream_response(self):
        @route('/')
        def stream(request):
            return StreamedResponse()

        res = do_req('/')
        self.assertNotIn('Content-Length', res.headers)
        self.assertEqual(res.get_html(), "Hello, world!")

    def test_placeholders(self):
        @route('/int/none')
        @route('/int/{value:d}')
        def route_int(request, value=None):
            return HTTPTextResponse(f"Value is {value}")

        @route('/str/fixed')
        @route('/str/{val1}/{val2}')
        def route_str(request, val1=None, val2=None):
            return HTTPTextResponse(f"Values are {val1} {val2}")

        self.assertFalse(route.is_valid_path('/int/'))
        self.assertTrue(route.is_valid_path('/int/1'))

        self.assertEqual(do_req('/int/none').get_html(), "Value is None")
        self.assertEqual(do_req('/int/1234').get_html(), "Value is 1234")

        self.assertEqual(route_int.get_path(params={}), '/int/none')
        self.assertEqual(route_int.get_path(params={'value': 42}), '/int/42')

        self.assertEqual(view_path(get_prepared_request(), route_int,
            value=456), '/somewhere/int/456')

        self.assertEqual(do_req('/str/fixed').get_html(),
            "Values are None None")
        self.assertEqual(do_req('/str/a/b/c/d').get_html(),
            "Values are a b/c/d")
        self.assertEqual(do_req('/str/a/b/c/d/').get_html(),
            "Values are a b/c/d/")

        self.assertEqual(route_str.get_path(params={}), '/str/fixed')
        self.assertEqual(route_str.get_path(params={
            'val1': 'abc',
            'val2': 'def'
        }), '/str/abc/def')

        with self.assertRaisesRegex(ValueError, r"No view route takes "
            r"parameters a, b"):
            route_str.get_path(params={'a': 123, 'b': 456})

    def test_redirect(self):
        @route('/new1')
        def route_dest(request):
            raise NotImplementedError()

        @route('/redirected_perm')
        def do_perm_redirect(request):
            return redirect_to_view(request, route_dest, permanent=True)

        @route('/redirected_temp')
        def do_temp_redirect(request):
            return redirect(request, '/new2', extra_headers={'X-Blah': 'Test'})

        perm_res = do_req('/redirected_perm')
        perm_res.check_redirect('301 Moved Permanently',
            'http://127.0.0.1/somewhere/new1')

        perm_temp = do_req('/redirected_temp')
        perm_temp.check_redirect('302 Found',
            'http://127.0.0.1/somewhere/new2')
        self.assertEqual(perm_temp.headers.get('X-Blah'), 'Test')

        # Also test HTTP_HOST support
        perm_res = do_req('/redirected_perm', {'HTTP_HOST': 'abc'})
        perm_res.check_redirect('301 Moved Permanently',
            'http://abc/somewhere/new1')

        perm_res = do_req('/redirected_perm', {'HTTP_HOST': '[1:2:3]'})
        perm_res.check_redirect('301 Moved Permanently',
            'http://[1:2:3]/somewhere/new1')

        perm_res = do_req('/redirected_perm', {'HTTP_HOST': '[1:2:3]:123'})
        perm_res.check_redirect('301 Moved Permanently',
            'http://[1:2:3]:123/somewhere/new1')

        perm_res = do_req('/redirected_perm', {'HTTP_HOST': '[1:2:3]:80'})
        perm_res.check_redirect('301 Moved Permanently',
            'http://[1:2:3]/somewhere/new1')

    def test_slash_redirect(self):
        @route('/static/{path}')
        def f(request):
            raise NotImplementedError()

        res = do_req('/static')
        self.assertIn('<a href="http://127.0.0.1/somewhere/static/">here</a>',
            res.get_html('301 Moved Permanently'))

    def test_url_build(self):
        req = get_prepared_request()
        self.assertEqual(req.prefix.build_url('/test', {'x': 'é#a?='}),
            'http://127.0.0.1/somewhere/test?x=%C3%A9%23a%3F%3D')

    def test_extensions(self):
        @route.request_extension
        def ext_func(req, view, extra_headers):
            if req.path == '/intercept':
                return HTTPTextResponse("Intercepted")

            extra_headers['X-Ext'] = "abcd"

        f_called = False

        @route('/{path}')
        def f(request, path):
            nonlocal f_called
            f_called = True
            return HTTPTextResponse("Normal response")

        self.assertEqual(do_req('/intercept').get_html(), "Intercepted")
        self.assertFalse(f_called)

        normal_req = do_req('/')
        self.assertEqual(normal_req.get_html(), "Normal response")
        self.assertTrue(f_called)
        self.assertEqual(normal_req.headers.get('X-Ext'), "abcd")

    def test_view_props(self):
        @route('/')
        def f(request):
            raise NotImplementedError()

        f.set_prop(ViewProp("test"))
        self.assertEqual(f.get_prop(ViewProp), ViewProp("test"))
        self.assertEqual(f.get_prop(str, "x"), "x")
        with self.assertRaises(KeyError):
            f.get_prop(str)

    def test_file_serve(self):
        with tempfile.TemporaryDirectory('test_web') as raw_path:
            base_path = pathlib.Path(raw_path)

            (base_path / 'some_dir').mkdir()
            (base_path / 'some_dir' / 'a_file.txt').write_text('Hello, world!')
            (base_path / 'some_dir' / 'other_file').write_bytes(b'abc')
            (base_path / 'some_dir' / 'unreadable').write_bytes(b'')
            (base_path / 'some_dir' / 'unreadable').chmod(000)
            s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            s.bind(str(base_path / 'some_dir' / 'a_socket.bin'))
            s.close()
            (base_path / 'some_dir' / 'null.bin').symlink_to('/dev/null')

            special_file = None
            @route("/static/special")
            def special_route(request):
                nonlocal special_file

                fdesc = (base_path / 'some_dir' / 'a_file.txt').open('rb')
                special_file = fdesc

                return HTTPFileResponse(fdesc, 'text/plain; charset=utf-8')

            @route("/static/{path}")
            def static_route(request, path):
                return HTTPFileResponse.serve_file(base_path, path)

            do_req('/static/').check_msg("403 Forbidden",
                "Directory listing is not allowed")

            do_req('/static/some_dir').check_msg("403 Forbidden",
                "Directory listing is not allowed")

            do_req('/static/some_dir/').check_msg("403 Forbidden",
                "Directory listing is not allowed")

            do_req('/static/..').check_msg("403 Forbidden",
                "Invalid path component")

            do_req('/static/../a/../..').check_msg("403 Forbidden",
                "Invalid path component")

            do_req('/static/nonexistent').check_msg("404 Not Found",
                "Nothing matches the given URI.")

            do_req('/static/some_dir/unreadable').check_msg("403 Forbidden",
                "Request forbidden -- authorization will not help.")

            do_req('/static/some_dir/a_socket.bin').check_msg("403 "
                "Forbidden", "The target is not a regular file")

            do_req('/static/some_dir/null.bin').check_msg("403 "
                "Forbidden", "The target is not a regular file")

            self.assertEqual(do_req('/static/some_dir/a_file.txt'),
                Response('200 OK', {
                    'Content-Type': 'text/plain; charset=utf-8',
                    'Cache-Control': 'no-cache',
                    'Content-Length': '13',
                },
                b"Hello, world!",
            ))

            self.assertEqual(do_req('/static/special'),
                Response('200 OK', {
                    'Content-Type': 'text/plain; charset=utf-8',
                    'Cache-Control': 'no-cache',
                    'Content-Length': '13',
                },
                b"Hello, world!",
            ))

            with patch('pyshellytemp.web.response.pathlib.Path.open') as mock:
                mock.side_effect = OSError("Generic error")
                with self.assertRaisesRegex(OSError, "Generic error"):
                    do_req('/static/some_dir/a_file.txt')

    def test_def_errors(self):
        with self.assertRaisesRegex(ValueError, "URL patterns must start with "
            "/"):
            @route("aaa")
            def bad(request):
                raise NotImplementedError()

        with self.assertRaisesRegex(ValueError, "Invalid placeholder 'a b'"):
            @route("/a/{a b}")
            def bad(request):
                raise NotImplementedError()

        with self.assertRaisesRegex(ValueError, "Placeholder 'x' used multiple "
            "times"):
            @route("/a/{x}/b/{x:d}")
            def bad(request):
                raise NotImplementedError()

    def setUp(self):
        route._views.clear()
        route._extensions.clear()



def do_req(path, extra_env=(), post=None, **kwargs):
    req_env = _get_req_env(path, extra_env, post, **kwargs)

    app = route.get_wsgi_app()
    response = Response()
    data = app(req_env, response)
    response.process(data)

    return response


def get_prepared_request(extra_env=()):
    return HTTPRequest.from_req(_get_req_env('/', extra_env))


def _get_req_env(path, extra_env=(), post=None, **kwargs):
    env = {
        'REQUEST_METHOD': 'GET',
        'SCRIPT_NAME': '/somewhere',
        'PATH_INFO': path,
        'REMOTE_ADDR': '127.0.0.1',
        'QUERY_STRING': urlencode(kwargs),
        'SERVER_NAME': '127.0.0.1',
        'SERVER_PORT': 80,
        'wsgi.url_scheme': 'http',
    }

    if post is not None:
        env['REQUEST_METHOD'] = 'POST'
        env['CONTENT_TYPE'] = 'application/x-www-form-urlencoded'
        data = io.BytesIO(urlencode(post).encode('ascii'))
        env['wsgi.input'] = data
        env['CONTENT_LENGTH'] = str(len(data.getvalue()))

    env.update(extra_env)

    return env


@dataclasses.dataclass
class ViewProp:
    val: str


class StreamedResponse:
    @property
    def status(self) -> HTTPStatus:
        return HTTPStatus.OK

    @property
    def content_type(self) -> str:
        return "text/html; charset=utf-8"

    @property
    def extra_headers(self) -> dict[str, str]:
        return {}

    def get_data(self):
        return -1, self._data_gen()

    def _data_gen(self):
        yield b"Hello, "
        yield b"world"
        yield b"!"


@dataclasses.dataclass
class Response:
    status: str = ""
    headers: dict[str, str] = dataclasses.field(default_factory=dict)
    response: bytes = b''

    def __call__(self, status, headers):
        assert not self.status, self.status

        self.status = status
        self.headers = dict(headers)

    def get_html(self, expected_status='200 OK'):
        assert self.status == expected_status, self

        c_type = self.headers.get('Content-Type', '')
        assert c_type == 'text/html; charset=utf-8', self

        return self.response.decode('utf-8')

    def check_msg(self, expected_status, expected_msg):
        html = self.get_html(expected_status)

        msg = "\n".join(line for line in html.split("\n")
            if line and not line.startswith("<"))

        assert msg == expected_msg, self

    def check_redirect(self, status, url):
        html = self.get_html(status)
        assert self.headers.get('Location') == url, self
        assert f'<a href="{url}">here</a>' in html, self

    def process(self, data):
        assert not self.response, self.response

        self.response = b''.join(data)
        close_func = getattr(data, 'close', None)
        if close_func is not None:
            close_func()

        expected_length = int(self.headers.get('Content-Length', '-1'))

        if expected_length >= 0:
            assert len(self.response) == expected_length, self
