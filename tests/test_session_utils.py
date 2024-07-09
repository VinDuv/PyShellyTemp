"""
Test of optional parts of the framework: render to response and sessions.
"""

from unittest.mock import create_autospec, patch, Mock
import contextlib
import datetime
import hashlib
import pathlib
import sqlite3
import tempfile
import unittest

from pyshellytemp import session
from pyshellytemp.db.access import Database
from pyshellytemp.db.orm import TableDef
from pyshellytemp.tpl_mgr import TemplateManager
from pyshellytemp.util import render, join_lines, float_or_default
from pyshellytemp.web import route, HTTPTextResponse
from tests.test_web import do_req


URL_PREFIX = 'http://127.0.0.1/somewhere'
URL_PREFIX_S = 'https://127.0.0.1/somewhere'
TPL_DIR = pathlib.Path(__file__).parent / 'data' / 'session_utils'


def fake_pbkdf2_hmac(_algo, data, salt, _iter):
    return hashlib.sha256(data + salt).digest()

@patch('pyshellytemp.session.hashlib.pbkdf2_hmac', fake_pbkdf2_hmac)
class SessionTests(unittest.TestCase):
    def test_session(self):
        with self._setup_framework():
            @session.login_required
            @route('/abcd')
            def main(request):
                username = session.User.from_request(request).username
                return HTTPTextResponse(f"Hello {username}")

            @session.login_required
            @route('/set_msg')
            def set_msg(request):
                session_data = request.get_ext(session.SessionData)
                session_data.set_next_message("Hello, world!")
                return HTTPTextResponse("Message set")

            @session.login_required
            @route('/get_msg')
            def get_msg(request):
                session_data = request.get_ext(session.SessionData)
                return HTTPTextResponse(session_data.message or '')

            @route('/defg')
            def regular_page(request):
                return render(request, 'regular.html', {},
                    headers={'X-Regular': 'yes'})

            @session.no_session
            @route('/no_session')
            def no_session(request):
                try:
                    session_data = request.get_ext(session.SessionData)
                except KeyError:
                    session_data = None

                assert session_data is None, session_data

                return HTTPTextResponse("OK")

            session.User.create_user('test', 'abcd')

            # No session, should redirect to login page
            do_req('/abcd').check_redirect('302 Found',
                f'{URL_PREFIX}/login?next=/abcd')

            do_req('/login', next='/abcd').check_msg("200 OK", "Login '' ''")

            # Try to login with non-existent user
            post = {'username': 'x', 'password': 'x'}
            res = do_req('/login', next='/abcd', post=post)
            res.check_msg("200 OK", "Login 'Invalid username or password.' 'x'")

            # Try to login with good user and bad password
            post = {'username': 'test', 'password': 'x'}
            res = do_req('/login', next='/abcd', post=post)
            res.check_msg("200 OK", "Login 'Invalid username or password.' "
                "'test'")
            self.assertNotIn('Set-Cookie', res.headers)

            mock_dt = create_autospec(datetime.datetime, spec_set=True)
            cur_dt = self._utc_as_local(2000, 1, 1, 0, 0, 0)
            mock_dt.configure_mock(**{
                'now.return_value': cur_dt,
                'fromtimestamp': datetime.datetime.fromtimestamp,
            })

            post = {'username': 'test', 'password': 'abcd'}
            with patch('pyshellytemp.session.datetime.datetime', mock_dt):
                res = do_req('/login', next='/abcd', post=post)
            res.check_redirect('302 Found', f'{URL_PREFIX}/abcd')
            set_cookie = res.headers['Set-Cookie']
            cookie_data = self._dump_set_cookie(res)
            sessid = cookie_data.pop('sessid')
            self.assertEqual(cookie_data, {
                'Domain': '127.0.0.1',
                'Path': '/somewhere/',
                'SameSite': 'Strict',
                'HttpOnly': '',
                'Expires': 'Mon, 31 Jan 2000 00:00:00 GMT',
            })

            sess = session.Session.get_one(sess_id=sessid)
            self.assertEqual(sess.last_activity, cur_dt)

            env = {
                'HTTP_COOKIE': 'sessid=abcdef'
            }

            # Try with invalid session ID, should still redirect to login
            with patch('pyshellytemp.session.datetime.datetime', mock_dt):
                do_req('/abcd', env).check_redirect('302 Found',
                    f'{URL_PREFIX}/login?next=/abcd')

            # With valid session ID, should succeed
            env['HTTP_COOKIE'] = f'sessid={sessid}'
            with patch('pyshellytemp.session.datetime.datetime', mock_dt):
                res = do_req('/abcd', env)
            res.check_msg("200 OK", "Hello test")
            self.assertNotIn('Set-Cookie', res.headers)

            # Move forward in time a bit
            cur_dt = self._utc_as_local(2000, 1, 1, 0, 3, 0)
            mock_dt.now.return_value = cur_dt

            # Normal access, cookie not refreshed (the regular page sets an
            # extra header though)
            with patch('pyshellytemp.session.datetime.datetime', mock_dt):
                res = do_req('/defg', env)
            res.check_msg("200 OK", "Regular page")
            self.assertNotIn('Set-Cookie', res.headers)
            self.assertEqual(res.headers['X-Regular'], 'yes')

            # Move forward again, cookie refreshed
            cur_dt = self._utc_as_local(2000, 1, 2, 0, 0, 0)
            mock_dt.now.return_value = self._utc_as_local(2000, 1, 2, 0, 0, 0)

            # Cookie (and session) refreshed
            with patch('pyshellytemp.session.datetime.datetime', mock_dt):
                res = do_req('/defg', env)
            res.check_msg("200 OK", "Regular page")

            self.assertEqual(self._dump_set_cookie(res), {
                'sessid': sessid,
                'Domain': '127.0.0.1',
                'Path': '/somewhere/',
                'SameSite': 'Strict',
                'HttpOnly': '',
                'Expires': 'Tue, 01 Feb 2000 00:00:00 GMT',
            })

            sess = session.Session.get_one(sess_id=sessid)
            self.assertEqual(sess.last_activity, cur_dt)

            # Message set/get
            with patch('pyshellytemp.session.datetime.datetime', mock_dt):
                do_req('/set_msg', env).check_msg("200 OK", "Message set")
                do_req('/get_msg', env).check_msg("200 OK", "Hello, world!")
                do_req('/get_msg', env).check_msg("200 OK", "")

            # No session
            with patch('pyshellytemp.session.datetime.datetime', mock_dt):
                do_req('/no_session', env).check_msg("200 OK", "OK")

            # Accessing the login page while logged in follows the redirect
            with patch('pyshellytemp.session.datetime.datetime', mock_dt):
                do_req('/login', env, next='/defg').check_redirect('302 Found',
                    f'{URL_PREFIX}/defg')

            # Ignore the next if it would loop
            with patch('pyshellytemp.session.datetime.datetime', mock_dt):
                do_req('/login', env, next='/login').check_redirect('302 Found',
                    f'{URL_PREFIX}/')

            # Access the logout page using GET
            with patch('pyshellytemp.session.datetime.datetime', mock_dt):
                do_req('/logout', env).check_msg("200 OK", "Logout page")

            # Should still be logged in
            with patch('pyshellytemp.session.datetime.datetime', mock_dt):
                res = do_req('/abcd', env)
            res.check_msg("200 OK", "Hello test")

            # Access the logout page using POST
            with patch('pyshellytemp.session.datetime.datetime', mock_dt):
                res = do_req('/logout', env, post={'logout': "Logout"})

            # Redirect to /
            res.check_redirect('302 Found', f'{URL_PREFIX}/')

            # Session cookie destroyed
            self.assertEqual(self._dump_set_cookie(res), {
                'sessid': '',
                'Domain': '127.0.0.1',
                'Path': '/somewhere/',
                'SameSite': 'Strict',
                'HttpOnly': '',
                'Expires': 'Thu, 01 Jan 1970 00:00:00 GMT',
            })

            del env['HTTP_COOKIE']

            # Database session destroyed
            self.assertIsNone(session.Session.get_opt(sess_id=sessid))

            # Logout with GET while not logged in; should redirect
            with patch('pyshellytemp.session.datetime.datetime', mock_dt):
                do_req('/logout', env).check_redirect('302 Found',
                    f'{URL_PREFIX}/')

            # Check that login with HTTP uses the secure cookie
            post = {'username': 'test', 'password': 'abcd'}
            env = {'wsgi.url_scheme': 'https', 'SERVER_PORT': '443'}
            with patch('pyshellytemp.session.datetime.datetime', mock_dt):
                res = do_req('/login', env, next='/abcd', post=post)
            res.check_redirect('302 Found', f'{URL_PREFIX_S}/abcd')

            cookie_data = self._dump_set_cookie(res)
            del cookie_data['sessid']
            self.assertEqual(cookie_data, {
                'Domain': '127.0.0.1',
                'Path': '/somewhere/',
                'SameSite': 'Strict',
                'HttpOnly': '',
                'Secure': '',
                'Expires': 'Tue, 01 Feb 2000 00:00:00 GMT',
            })

    def test_nonreg_session_disco_when_refresh(self):
        with self._setup_framework():
            @route('/')
            def main(request):
                return HTTPTextResponse(f"Need at least a route")

            session.User.create_user('test', 'abcd')

            mock_dt = create_autospec(datetime.datetime, spec_set=True)
            cur_dt = self._utc_as_local(2000, 1, 1, 0, 0, 0)
            mock_dt.configure_mock(**{
                'now.return_value': cur_dt,
                'fromtimestamp': datetime.datetime.fromtimestamp,
            })

            post = {'username': 'test', 'password': 'abcd'}
            with patch('pyshellytemp.session.datetime.datetime', mock_dt):
                res = do_req('/login', next='/', post=post)
            res.check_redirect('302 Found', f'{URL_PREFIX}/')
            set_cookie = res.headers['Set-Cookie']
            cookie_data = self._dump_set_cookie(res)
            sessid = cookie_data.pop('sessid')
            self.assertEqual(cookie_data, {
                'Domain': '127.0.0.1',
                'Path': '/somewhere/',
                'SameSite': 'Strict',
                'HttpOnly': '',
                'Expires': 'Mon, 31 Jan 2000 00:00:00 GMT',
            })

            sess = session.Session.get_one(sess_id=sessid)
            self.assertEqual(sess.last_activity, cur_dt)

            env = {
                'HTTP_COOKIE': f'sessid={sessid}',
            }

            # Move forward in time so the cookie needs to be refreshed
            cur_dt = self._utc_as_local(2000, 1, 2, 0, 0, 0)
            mock_dt.now.return_value = self._utc_as_local(2000, 1, 2, 0, 0, 0)

            # Access logout page
            with patch('pyshellytemp.session.datetime.datetime', mock_dt):
                res = do_req('/logout', env, post={'logout': "Logout"})

            # Redirect to /
            res.check_redirect('302 Found', f'{URL_PREFIX}/')

            # Session cookie destroyed
            self.assertEqual(self._dump_set_cookie(res), {
                'sessid': '',
                'Domain': '127.0.0.1',
                'Path': '/somewhere/',
                'SameSite': 'Strict',
                'HttpOnly': '',
                'Expires': 'Thu, 01 Jan 1970 00:00:00 GMT',
            })

            del env['HTTP_COOKIE']

            # Database session destroyed
            self.assertIsNone(session.Session.get_opt(sess_id=sessid))

    def test_user(self):
        with self._setup_framework():
            session.User.create_user('test', 'abcd')

            user = session.User.try_login_user('test', 'abcd')
            self.assertIsNotNone(user)

            user.set_password('defg')
            self.assertIsNone(session.User.try_login_user('test', 'abcd'))
            user = session.User.try_login_user('test', 'defg')
            self.assertIsNotNone(user)

    def test_def_errors(self):
        with self._setup_framework():
            with self.assertRaisesRegex(ValueError, "Put the login_required "
                "decorator on the top of the route decorator"):
                @route('/')
                @session.login_required
                def bad_decl(request):
                    raise NotImplementedError()

            with self.assertRaisesRegex(ValueError, "Put the no_session "
                "decorator on the top of the route decorator"):
                @route('/')
                @session.no_session
                def bad_decl(request):
                    raise NotImplementedError()

    @contextlib.contextmanager
    def _setup_framework(self):
        my_db = Database(':memory:')
        my_db.register_init_hook(priority=0)(TableDef._create_tables)

        my_db.init()

        with patch('pyshellytemp.tpl_mgr.TPL_DIR', TPL_DIR):
            with patch('pyshellytemp.tpl_mgr.os.environ', {}):
                tpl_mgr = TemplateManager.create()

        route._views.clear()
        route._extensions.clear()

        route.request_extension(session._session_request_extension)
        route('/login')(session.login)
        route('/logout')(session.logout)

        with patch('pyshellytemp.util.templates', tpl_mgr), \
            patch('pyshellytemp.db.orm.database', my_db):
            yield

    @staticmethod
    def _dump_set_cookie(response):
        cookie_data = {}
        for item in response.headers['Set-Cookie'].split('; '):
            key, _, value = item.partition('=')
            cookie_data[key] = value

        return cookie_data

    @staticmethod
    def _utc_as_local(year, month, day, hour=0, minute=0, second=0):
        dt = datetime.datetime(year, month, day, hour, minute, second,
            tzinfo=datetime.timezone.utc)
        return dt.astimezone(None).replace(tzinfo=None)


class UtilTests(unittest.TestCase):
    def test_join_lines(self):
        lines = [
            "Line 1<",
            "Line 2&",
            "Line 3>",
        ]

        res = join_lines(lines)

        self.assertEqual(res.to_html(), "Line 1&lt;<br />\nLine 2&amp;<br />\n"
            "Line 3&gt;")

    def test_float_or_default(self):
        self.assertEqual(float_or_default(None, default=42.0), 42.0)
        self.assertEqual(float_or_default("aaaa", default=42.0), 42.0)
        self.assertEqual(float_or_default("32.1", default=42.0), 32.1)
