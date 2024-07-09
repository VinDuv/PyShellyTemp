"""
HTTP request decoding and formatting
"""

from http import HTTPStatus, cookies
from urllib.parse import parse_qs, urlencode
import abc
import dataclasses
import io
import typing

from .response import HTTPError


class RequestBodyWrapper(io.IOBase):
    """
    Wraps the wsgi.input file object to ensure that all available data from the
    request, and no more, is read.
    """

    def __init__(self, file_obj: typing.BinaryIO, length: int) -> None:
        super().__init__()
        self._file_obj = file_obj
        self._remaining = max(0, length)

    def read(self, size: int = -1, /) -> bytes:
        """
        Read size bytes from the input and return them. If the size is not
        specified or negative, returns the rest of the input.
        This may return less data than expected if the request body was
        truncated or if no data is available.
        """

        if size < 0:
            size = self._remaining

        return b''.join(self._read_bytes(size))

    def close(self) -> None:
        for _ in self._read_bytes(self._remaining):
            pass

        super().close()

    def _read_bytes(self, size: int) -> typing.Iterable[bytes]:
        """
        Yields parts of the remaining data in the request body, until the
        request size was read. This may return a short read if the request
        body was truncated.
        """

        while size > 0:
            data = self._file_obj.read(min(self._remaining, size))
            if not data:
                # Truncated request body
                self._remaining = 0
                break

            data_len = len(data)
            self._remaining -= data_len
            size -= data_len
            yield data


@dataclasses.dataclass(frozen=True)
class POSTData:
    """
    Holds the POST data sent by the client.
    """

    MAX_FORM_SIZE = 512 * 1024

    content_type: str
    content_length: int
    post_input: RequestBodyWrapper

    def get_form_data(self) -> dict[str, str]:
        """
        Extracts the POST data as form data. The keys and values are decoded
        as UTF-8 and put in a dictionary. An HTTPError is raised on decode
        issues or other protocol issues.
        """

        if self.content_type != 'application/x-www-form-urlencoded':
            raise HTTPError(HTTPStatus.BAD_REQUEST, "Unexpected form type")

        if self.content_length > self.MAX_FORM_SIZE:
            raise HTTPError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "Form too large")

        data = self.post_input.read(self.content_length)

        assert data is not None

        if len(data) != self.content_length:
            raise HTTPError(HTTPStatus.BAD_REQUEST, "Truncated request content")

        try:
            raw_data = parse_qs(data, keep_blank_values=True,
                strict_parsing=True)

            result: dict[str, str] = {}

            for raw_key, raw_values in raw_data.items():
                result[raw_key.decode('utf-8')] = raw_values[-1].decode('utf-8')
        except ValueError:
            raise HTTPError(HTTPStatus.BAD_REQUEST, "Bad form data") from None

        return result

    @classmethod
    def from_req(cls, environ: dict[str, typing.Any]) -> typing.Self | None:
        """
        Constructs a POSTData object from the specified request data. If this
        is a GET request, returns None. If the request has invalid specifiers,
        an HTTPError is raised.
        """

        content_type = environ.get('CONTENT_TYPE', '')

        try:
            content_length = int(environ.get('CONTENT_LENGTH', '-1'))
        except ValueError:
            content_length = -1

        wrapper = RequestBodyWrapper(environ['wsgi.input'], content_length)

        if environ['REQUEST_METHOD'] != 'POST':
            # If data was given with the GET request (??), drain the wrapper
            wrapper.close()
            return None


        if not content_type or content_length < 0:
            wrapper.close()
            raise HTTPError(HTTPStatus.BAD_REQUEST, "Bad POST request headers")

        return cls(content_type, content_length, wrapper)


@dataclasses.dataclass(frozen=True)
class RequestPrefix:
    """
    Parts of the URL that are common to all views of the application. Can be
    used to reconstruct a full URL for a redirection.
    """

    # Request protocol, 'http' or 'https'
    protocol: str

    # Server hostname
    server_host: str

    # Server port (None if the standard port is used)
    server_port: int | None

    # Part of the URL that is global to the application
    path: str

    @classmethod
    def from_req(cls, environ: dict[str, typing.Any]) -> typing.Self:
        """
        Creates a request prefix from the specified request environment.
        """

        protocol: str = environ['wsgi.url_scheme']
        server_host: str = environ['SERVER_NAME']
        server_port: int | None = int(environ['SERVER_PORT'])

        http_full_host: str = environ.get('HTTP_HOST', '')
        if http_full_host:
            # Try to separate the host and the port; this needs to handle
            # '[1:2:3]' and '[1:2:3]:80'
            http_host, separator, http_port_str = http_full_host.rpartition(':')

            try:
                http_port = int(http_port_str, 10)
            except ValueError:
                http_port = 0

            if separator and http_port > 0:
                # Successfully split an hopefully valid port
                server_host = http_host
                server_port = http_port
            else:
                # Could not split the port, assume the HTTP host does not
                # include it
                server_host = http_full_host

        if ((protocol == 'http' and server_port == 80) or (protocol == 'https'
            and server_port == 443)):
            server_port = None

        path: str = environ['SCRIPT_NAME']

        return cls(protocol, server_host, server_port, path)

    def build_url(self, app_path: str, query_params: dict[str, typing.Any] |
        None = None) -> str:
        """
        Builds a full URL from the specified application path. This path
        must start with a slash.
        """

        if query_params:
            param_str = '?' + urlencode([(key, value)
                for key, value in query_params.items()
                if value not in {'', None}])
        else:
            param_str = ''

        return (f"{self.protocol}://{self.server}{self.path}{app_path}"
            f"{param_str}")

    @property
    def server(self) -> str:
        """
        Returns the server hostname used by the request, followed by :port if a
        non-standard port is used.
        """

        if self.server_port:
            return f'{self.server_host}:{self.server_port}'

        return self.server_host


class ReqExtData(abc.ABC):
    """
    Request extension data. A request extension can instantiate a subclass
    of this class to associate data to the handled request.
    This data can then be transferred to a template context in order to be
    rendered in a page.
    """

    def put_into_context(self, context: dict[str, typing.Any]) -> None:
        """
        Put the data stored in the object into the render context dictionary.
        """

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>"


ExData = typing.TypeVar('ExData', bound=ReqExtData)


@dataclasses.dataclass(frozen=True)
class HTTPRequest:
    """
    Represents an HTTP request.
    """

    # URL prefix parts
    prefix: RequestPrefix

    # Part of the URL that indicates the resource within the application
    path: str

    # Request headers, case-normalized (title case with dashes)
    headers: dict[str, str]

    # POST data, None if this is a GET request
    post: POSTData | None

    # Query string, as a dictionary
    query: dict[str, str]

    # Remote address
    remote_addr: str

    @classmethod
    def from_req(cls, environ: dict[str, typing.Any]) -> typing.Self:
        """
        Creates a request object from the specified request environment.
        """

        post = POSTData.from_req(environ)

        method: str = environ['REQUEST_METHOD']
        if method not in {'GET', 'POST'}:
            # If there were data in the request, it was already drained
            # by POSTData.from_req.
            raise HTTPError(HTTPStatus.METHOD_NOT_ALLOWED,
                f"Unknown method {method!r}")

        prefix = RequestPrefix.from_req(environ)

        path: str = environ['PATH_INFO']

        remote_addr = environ['REMOTE_ADDR']

        headers = {}
        for key, value in environ.items():
            parts = key.split('_')
            if parts[0] != 'HTTP':
                continue

            key = '-'.join(part.title() for part in parts[1:])
            headers[key] = value

        query = {
            key: values[0]
            for key, values in parse_qs(environ['QUERY_STRING']).items()
        }

        return cls(prefix, path, headers, post, query, remote_addr)

    def get_cookies(self) -> dict[str, str]:
        """
        Returns the cookies included in the request.
        Raises a Bad Request error if the Cookie header is invalid.
        """

        raw_cookies = self.headers.pop('Cookie', '')

        return {
            key: morsel.value
            for key, morsel in cookies.SimpleCookie(raw_cookies).items()
        }

    # Values for request extension data (do not use directly)
    _ext_values: dict[type[ReqExtData], ReqExtData] = dataclasses.field(
        default_factory=dict)

    def get_ext(self, val_type: typing.Type[ExData]) -> ExData:
        """
        Returns the value that a request extension has stored in the request.
        The extension is identified by its class; always returns an instance of
        the class, or raise a KeyError if the request extension has not set any
        data.
        """

        return typing.cast(ExData, self._ext_values[val_type])

    def set_ext(self, value: ExData) -> None:
        """
        Called by a request extension to store a value in the request.
        The extension is identified by the value’s class.
        """

        self._ext_values[type(value)] = value

    def get_context(self) -> dict[str, typing.Any]:
        """
        Creates a returns a context dictionary including data from the request
        extensions.
        """

        context: dict[str, typing.Any] = {
            'urlprefix': self.prefix.path,
            'urlpath': self.path,
        }

        for ext_data in self._ext_values.values():
            ext_data.put_into_context(context)

        return context

    def drain_request_body(self) -> None:
        """
        Finishes reading the request body, if any.
        """

        post = self.post
        if post is not None:
            post.post_input.close()
