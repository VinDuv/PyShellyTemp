"""
HTTP response formatting
"""

from http import HTTPStatus
import dataclasses
import errno
import html
import mimetypes
import os
import pathlib
import stat
import typing


# Allowed formats for an HTTP response
HTTPResponseData: typing.TypeAlias = typing.Iterable[bytes] | typing.BinaryIO


class HTTPResponse(typing.Protocol):
    """
    A response to an HTTP request.
    """

    @property
    def status(self) -> HTTPStatus:
        """
        Response status
        """

    @property
    def content_type(self) -> str:
        """
        Response content-type
        """

    @property
    def extra_headers(self) -> dict[str, str]:
        """
        Extra response headers (not including Content-Type or Content-Length)
        """

    def get_data(self) -> tuple[int, HTTPResponseData]:
        """
        Get the response data, either as a iterable of byte strings, or an open
        file.

        The returned integer is the size, in bytes, of the entire reply; it can
        be negative if the size is not known.
        """


@dataclasses.dataclass(frozen=True)
class HTTPTextResponse:
    """
    A text response to an HTTP request.
    """

    # The response text
    text: str

    # The response status
    status: HTTPStatus = HTTPStatus.OK

    # The response content type (HTML by default); should indicate UTF-8 charset
    content_type: str = 'text/html; charset=utf-8'

    # The response extra headers (usually not needed)
    extra_headers: dict[str, str] = dataclasses.field(default_factory=dict)

    def get_data(self) -> tuple[int, HTTPResponseData]:
        """
        Return the response as bytes.
        """

        data = self.text.encode('utf-8')
        return len(data), [data]

    @classmethod
    def msg_page(cls, status: HTTPStatus, contents: str = '',
        extra_headers: dict[str, str] | None = None) -> typing.Self:
        """
        Returns a HTTPTextResponse containing a basic HTML page with the
        specified message, using the HTTP status as title. If the message is not
        provided or is blank, the HTTP status description is used.
        """

        if not contents:
            contents = f"{status.description}."

        text = '\n'.join(cls._format_page(status.phrase, contents))
        if extra_headers is None:
            extra_headers = {}

        return cls(text, status, extra_headers=extra_headers)

    @staticmethod
    def _format_page(title: str, message: str) -> typing.Iterator[str]:
        """
        Yields lines of a formatted HTML page with the specified title and
        message.
        """

        title = html.escape(title, quote=False)

        yield "<!DOCTYPE html>"
        yield "<html>"
        yield "<body>"
        yield f"<h1>{title}</h1>"
        yield message
        yield "</body>"
        yield "</html>\n"


@dataclasses.dataclass(frozen=True)
class HTTPFileResponse:
    """
    Encapsulates a binary file to be returned as a response.
    """

    # The open file to be returned; will be closed when the response is
    # dispatched.
    fdesc: typing.BinaryIO

    # The file content type
    content_type: str

    # The file size (negative if not known; will be determined by fstat in that
    # case)
    file_size: int = -1

    # The response status
    status: HTTPStatus = HTTPStatus.OK

    # The response extra headers (usually not needed)
    extra_headers: dict[str, str] = dataclasses.field(default_factory=dict)

    def get_data(self) -> tuple[int, HTTPResponseData]:
        """
        Return the response as bytes.
        """

        size = self.file_size
        if size < 0:
            size = os.fstat(self.fdesc.fileno()).st_size

        return size, self.fdesc

    @classmethod
    def serve_file(cls, base_path: pathlib.Path, rel_path: str) -> HTTPResponse:
        """
        Serves the file at the relative path rel_path from the base path
        base_path.

        - If the relative path contains path components starting with ., or if
          the target is not a file, a Forbidden error response is returned.
        - If the target is not found, a Not Found error response is returned.
        - If the target is a file, a HTTPFileResponse is returned.
        """

        if rel_path.endswith('/'):
            return HTTPTextResponse.msg_page(HTTPStatus.FORBIDDEN,
                "Directory listing is not allowed")

        path = base_path
        for component in rel_path.split('/'):
            if not component:
                # //, do nothing
                continue

            if component.startswith('.'):
                return HTTPTextResponse.msg_page(HTTPStatus.FORBIDDEN,
                    "Invalid path component")

            path = path / component

        content_type, _ = mimetypes.guess_type(path, strict=False)
        if content_type is None:
            content_type = 'application/octet-stream'

        if content_type.startswith('text/'):
            content_type = f'{content_type}; charset=utf-8'

        return cls._serve_file(path, content_type)

    @classmethod
    def _serve_file(cls, path: pathlib.Path, content_type: str) -> HTTPResponse:
        """
        Serves the file at the specified path, after checking that it’s a
        regular file.
        """

        try:
            fdesc = path.open('rb')
        except IsADirectoryError:
            return HTTPTextResponse.msg_page(HTTPStatus.FORBIDDEN,
                "Directory listing is not allowed")
        except FileNotFoundError:
            return HTTPTextResponse.msg_page(HTTPStatus.NOT_FOUND)
        except PermissionError:
            return HTTPTextResponse.msg_page(HTTPStatus.FORBIDDEN)
        except OSError as err:
            if err.errno == errno.EOPNOTSUPP:
                return HTTPTextResponse.msg_page(HTTPStatus.FORBIDDEN,
                    "The target is not a regular file")
            raise

        info = os.fstat(fdesc.fileno())
        if not stat.S_ISREG(info.st_mode):
            fdesc.close()
            return HTTPTextResponse.msg_page(HTTPStatus.FORBIDDEN,
                "The target is not a regular file")

        return cls(fdesc, content_type, info.st_size)


class HTTPBaseError(Exception):
    """
    An exception with an attached HTTP response. If this exception is thrown
    by a view, the attached response is returned to the client that requested
    the view.
    """

    response: HTTPResponse

    def __init__(self, response: HTTPResponse):
        status = response.status
        super().__init__(f"{status.value} {status.phrase}")
        self.response = response


class HTTPError(HTTPBaseError):
    """
    Typical HTTP response error. Takes a status and an optional error message
    that will be returned in an HTML page.
    """

    def __init__(self, status: HTTPStatus, message: str = ''):
        response = HTTPTextResponse.msg_page(status, html.escape(message,
            quote=False))

        super().__init__(response)
