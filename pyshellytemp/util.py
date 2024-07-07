"""
Utility functions
"""

from http import HTTPStatus
import html
import typing

from .tpl_mgr import templates, SafeString
from .web import HTTPRequest, HTTPTextResponse

StrDict: typing.TypeAlias = dict[str, typing.Any]

__all__ = ['render', 'join_lines', 'float_or_default']

def render(request: HTTPRequest, tpl_path: str, context: StrDict | None = None,
    *, status: HTTPStatus = HTTPStatus.OK, headers: StrDict | None
    = None) -> HTTPTextResponse:
    """
    Renders a template into an HTTP response that can be returned from a view.
    """

    full_context = request.get_context()
    if context is not None:
        full_context.update(context)

    if headers is None:
        headers = {}

    content_type = headers.pop('Content-Type',
        'text/html; charset=utf-8')

    text = templates.get(tpl_path).render(full_context)

    return HTTPTextResponse(text, status, content_type, headers)


def join_lines(lines: list[str]) -> SafeString:
    """
    Format a list of lines of text to be rendered as HTML. The individual line
    contents is HTML-escaped.
    """

    return SafeString("<br />\n".join(html.escape(line) for line in lines))


def float_or_default(value: str | None, *, default: float) -> float:
    """
    Converts a given optional string value to a float. If the value is None
    or not a float, the default value is returned instead.
    """

    if value is None:
        return default

    try:
        return float(value)
    except ValueError:
        return default
