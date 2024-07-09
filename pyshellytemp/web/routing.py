"""
Defines view that accept a routed request and return a response.
"""

from http import HTTPStatus
import dataclasses
import html
import itertools
import re
import typing

from ..log_conf import configure_logging
from .request import HTTPRequest
from .response import HTTPBaseError, HTTPResponse, HTTPTextResponse
from .response import HTTPResponseData


def redirect(request: HTTPRequest, path: str,
    extra_headers: dict[str, str] | None = None, *,
    permanent: bool = False) -> HTTPResponse:
    """
    Generates a HTTP response that redirects to another page.
    """

    dest_url = request.prefix.build_url(path)
    headers = {
        'Location': dest_url,
    }

    if extra_headers is not None:
        headers.update(extra_headers)

    if permanent:
        status = HTTPStatus.MOVED_PERMANENTLY
    else:
        status = HTTPStatus.FOUND

    escaped_url = html.escape(dest_url)
    message = f"This resource has moved <a href=\"{escaped_url}\">here</a>."

    return HTTPTextResponse.msg_page(status, message, headers)


@dataclasses.dataclass(frozen=True)
class URLMatcher:
    """
    Matches a URL to find the associated view. The matching pattern is a string
    containing identifiers enclosed in {}. These identifiers are extracted from
    a matching URL and returned into a dictionary.
     - A placeholder in the middle of the URL will match any non-/ characters
       (1 character minimum)
     - A placeholder at the very end of the URL will match any characters
       (including zero characters)
     - A placeholder suffixed with :d will match a positive integer; the
       extracted result will be provided as an int instead of a string.

    This method can also generate a URL string by filling the placeholders with
    the provided values.
    """

    # Regular expression used to match a URL against the view
    pattern: typing.Pattern[str]

    # Parts of the URL that are not placeholders
    fixed_parts: list[str]

    # Placeholder names, in the order they appear in the URL. Their value
    # is True iff the placeholder accepts an integer value.
    placeholders: dict[str, bool]

    PLACEHOLDER_RE = re.compile(r'{(.*?)}')
    IDENT_RE = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_]*)(:d)?$')

    @classmethod
    def from_pattern(cls, pattern: str) -> typing.Self:
        """
        Creates a matcher from the provided pattern.
        """

        fixed_parts: list[str] = []
        placeholders: dict[str, bool] = {}
        regex_parts: list[str] = ['^']

        if len(pattern) < 1 or pattern[0] != '/':
            raise ValueError("URL patterns must start with /")

        cls._parse_pattern_into(pattern, fixed_parts, placeholders, regex_parts)

        regex_parts.append('$')

        compiled_pattern = re.compile(''.join(regex_parts))

        return cls(compiled_pattern, fixed_parts, placeholders)

    def match(self, url: str) -> dict[str, str | int] | None:
        """
        Matches a URL path against the pattern. If successful, returns a
        dictionary containing the placeholder values (possibly empty if there
        are not placeholders!)
        Returns None if the URL did not match the pattern.
        """

        match = self.pattern.match(url)
        if match is None:
            return None

        result: dict[str, str | int] = {}
        for key, value in match.groupdict().items():
            if self.placeholders[key]:
                result[key] = int(value, 10)
            else:
                result[key] = value

        return result

    def generate(self, params: dict[str, str | int]) -> str:
        """
        Generate a URL matching the pattern, with the pattern placeholders
        filled with the parameter values.
        Note that the filled values are not validated; the resulting URL may
        not match the pattern if these values are invalid.
        If a parameter is missing, raises a KeyError.
        """

        return ''.join(self._generate(params))

    @classmethod
    def _parse_pattern_into(cls, pattern: str, fixed_parts: list[str],
        placeholders: dict[str, bool], regex_parts: list[str]) -> None:
        """
        Parse the provided pattern string, and fills the provided parameters
        """

        pat_len = len(pattern)

        # Start position of the last fixed part
        fixed_pos = 0

        for match in cls.PLACEHOLDER_RE.finditer(pattern):
            # Identify the next placeholder and check it
            raw_ident = match.group(1)
            ident_match = cls.IDENT_RE.match(raw_ident)
            if ident_match is None:
                raise ValueError(f"Invalid placeholder {raw_ident!r}") from None
            ident, suffix = ident_match.groups()

            # Add the fixed part (text before the placeholder)
            # Added even if empty because the generation code expects the fixed
            # parts and placeholders to alternate
            start, end = match.span()
            fixed_part = pattern[fixed_pos:start]

            fixed_parts.append(fixed_part)
            regex_parts.append(re.escape(fixed_part))

            # Validate and add the placeholder
            if ident in placeholders:
                raise ValueError(f"Placeholder {ident!r} used multiple times")

            if suffix:
                # Integer matching
                regex_parts.append(rf'(?P<{ident}>\d{{1,20}})')
                placeholders[ident] = True
            else:
                if end == pat_len:
                    # String matching all
                    regex_parts.append(rf'(?P<{ident}>.*)')
                else:
                    # String matching 1+ non-slash
                    regex_parts.append(rf'(?P<{ident}>[^/]+)')
                placeholders[ident] = False

            fixed_pos = end

        # Add the fixed part at the end of the string
        fixed_part = pattern[fixed_pos:]
        fixed_parts.append(fixed_part)
        regex_parts.append(re.escape(fixed_part))

    def _generate(self, params: dict[str, str | int]) -> typing.Iterator[str]:
        """
        Yields parts of a URL matching the parameters with filled placeholders.
        """

        for fixed, ident in itertools.zip_longest(self.fixed_parts,
            self.placeholders.keys()):
            if fixed:
                yield fixed
            if ident is not None:
                yield str(params[ident])


# Type of a function that can be used as a view
# Note: the first parameter of the view function is always a HTTPRequest, and
# the other parameters must match the view placeholders, but this cannot be
# indicated in the type declaration.
ViewCallable: typing.TypeAlias = typing.Callable[..., HTTPResponse]
ViewOrFunc: typing.TypeAlias = ViewCallable | 'View'

# Type of a request extension; will be called with the request before the
# view function is called. The View is passed so the extension can recover
# properties from it. Can intercept the request and return its own response,
# and can add response headers.
ReqExtFunc: typing.TypeAlias = typing.Callable[[HTTPRequest, 'View',
    dict[str, str]], HTTPResponse | None]

# Property attached to a view by an extension
ViewProp = typing.TypeVar('ViewProp')


@dataclasses.dataclass(frozen=True)
class View:
    """
    Associates a URL matcher with a view function, so that a request can
    be dispatched to the appropriate function.
    """

    matcher: URLMatcher
    view_func: ViewCallable

    # View properties; all views that are attached to the same function share
    # their properties
    _props: dict[type, typing.Any]

    # Parent view, in the case of views stacked on a function
    parent_view: typing.Optional['View']

    def dispatch(self, request: HTTPRequest,
        extensions: list[ReqExtFunc]) -> HTTPResponse | None:
        """
        Checks if the request’s URL matches the view pattern, and if that is the
        case, call the request extensions, then dispatches the request and the
        URL placeholders into the view function. The function call result (a
        HTTPResponse, hopefully) is returned.

        If the URL does not match, None is returned.
        """

        extra_headers: dict[str, str] = {}

        values = self.matcher.match(request.path)
        if values is None:
            return None

        for extension in extensions:
            response = extension(request, self, extra_headers)
            if response is not None:
                # Request intercepted by the extension
                return response

        response = self.view_func(request, **values)
        response_headers = response.extra_headers

        for key, value in extra_headers.items():
            # Extensions can not override headers set by the view
            if key not in response_headers:
                response_headers[key] = value

        return response

    def check_url(self, path: str) -> bool:
        """
        Returns True iff the provided URL matches the view pattern.
        """

        # The return value of match can be a false value (empty dict) in case of
        # a successful match, compare to None instead
        return self.matcher.match(path) is not None

    def get_path(self, params: dict[str, str | int]) -> str:
        """
        Returns a path to the view, with the placeholder replaced with the
        parameter values.
        All views attached to a view function are checked; the first one (from
        top to bottom in the decorator order) which accepts the given parameters
        is used.
        """

        param_keys = params.keys()
        cur_view: View | None = self
        while cur_view is not None:
            matcher = cur_view.matcher
            if matcher.placeholders.keys() == param_keys:
                return matcher.generate(params)

            cur_view = cur_view.parent_view

        expected = ', '.join(params.keys())
        raise ValueError(f"No view route takes parameters {expected}.")

    def get_prop(self, val_type: typing.Type[ViewProp],
        default: ViewProp | None = None) -> ViewProp:
        """
        Returns a view property that was attached by an extension.
        """

        try:
            return typing.cast(ViewProp, self._props[val_type])
        except KeyError:
            if default is not None:
                return default

            raise

    def set_prop(self, value: ViewProp) -> None:
        """
        Called by an extension to set a property on the view.
        """

        val_type = type(value)

        if val_type in self._props:
            raise AssertionError(f"Attempting to register property {val_type} "
                f"multiple times. Views attached to the same function share "
                f"their properties.")

        self._props[val_type] = value

    def create_child(self, matcher: URLMatcher) -> 'View':
        """
        Returns a copy of the view with a different URL matcher.
        The view properties are shared between the views, and the created view’s
        parent is set to this view.
        """

        return View(matcher, self.view_func, self._props, self)

    @classmethod
    def create(cls, pattern: str, view_or_func: ViewOrFunc) -> 'View':
        """
        Creates and returns a View object, from a pattern and either:
         - A ViewCallable that will be called if the URL matches
         - An existing View object; this is used to assign multiple URLs to the
           same view function.
        """

        matcher = URLMatcher.from_pattern(pattern)

        if isinstance(view_or_func, View):
            return view_or_func.create_child(matcher)

        return cls(matcher, view_or_func, {}, None)


def view_path(request: HTTPRequest, view: View, **kwargs: int | str) -> str:
    """
    Generates a URL path that redirects to the specified view with the specified
    parameters. The URL is relative to the domain (i.e. it starts with /) so it
    can only be used for internal navigation.
    """

    return request.prefix.path + view.get_path(kwargs)

def redirect_to_view(request: HTTPRequest, view: View,
    extra_headers: dict[str, str] | None = None, *, permanent: bool = False,
    **kwargs: int | str) -> HTTPResponse:
    """
    Generates a URL that redirects to the specified view with the specified
    parameters, and returns a redirect response with this URL.
    """

    path = view.get_path(kwargs)

    return redirect(request, path, extra_headers, permanent=permanent)


# Type of the WSGI entry point start_response function
StartFunc: typing.TypeAlias = typing.Callable[[str, list[tuple[str, str]]],
    typing.Any]


@dataclasses.dataclass(frozen=True)
class Router:
    """
    Allows functions to be associated to a URL and registered as views (via the
    decorator syntax).
    Once the views are registered, an HTTP request can be dispatched to the
    appropriate view and its result returned back.
    This also provides a directly usable WSGI entry point.
    """

    # Registered views
    _views: list[View] = dataclasses.field(default_factory=list)

    # Registered request extensions
    _extensions: list[ReqExtFunc] = dataclasses.field(default_factory=list)

    def get_wsgi_app(self) -> typing.Callable[
        [dict[str, typing.Any], StartFunc], HTTPResponseData]:
        """
        Returns the WSGI entry point callable that will handle the requests.
        This checks that at least one view was registered.
        """

        if not self._views:
            raise AssertionError("No views are loaded.")

        configure_logging()

        return self._wsgi_app

    def dispatch(self, request: HTTPRequest) -> HTTPResponse:
        """
        Tries all registered views to handle the request, and returns the
        response.
        if no view match, and the request path does not end with a slash, try
        to add one. If a match is found, a redirect to the new URL is returned.
        If nothing is found, a Not Found response is returned.
        """

        for view in self._views:
            response = view.dispatch(request, self._extensions)
            if response is not None:
                return response

        if not request.path.endswith('/'):
            checked_path = request.path + '/'
            for view in self._views:
                if view.check_url(checked_path):
                    return redirect(request, checked_path, permanent=True)

        return HTTPTextResponse.msg_page(HTTPStatus.NOT_FOUND)

    def is_valid_path(self, path: str) -> bool:
        """
        Checks that a path (within the application) corresponds to a valid view.
        """

        for view in self._views:
            if view.check_url(path):
                return True

        return False

    def register(self, pattern: str, view_or_func: ViewOrFunc) -> View:
        """
        Registers a view function (specified either directly or by an existing
        View object) and a pattern.
        Returns the registered View object.
        """

        view = View.create(pattern, view_or_func)
        self._views.append(view)

        return view

    def request_extension(self, extension: ReqExtFunc) -> ReqExtFunc:
        """
        Registers a function as a request extension. Can be used as a decorator.
        """

        self._extensions.append(extension)

        return extension

    def to_class_method(self, pattern: str) -> typing.Callable[
        [type[typing.Any]], View]:
        """
        Enables the decorator syntax to register a class as a view. The class
        needs to have a handle_request class method.
        """

        # This should check if the passed type has a handle_request class
        # method with a request parameter, possibly followed by other
        # parameters, but this does not seem to be currently representable in
        # the type system.

        def _router_inner(klass: type[typing.Any]) -> View:
            return self.register(pattern, klass.handle_request)

        return _router_inner

    def __call__(self, pattern: str) -> typing.Callable[[ViewOrFunc], View]:
        """
        Enables the decorator syntax to register a view function:

        # route is a Router instance
        @route('/some/url/{idx:d}')
        def url_func(request: HTTPRequest, idx: int) -> HTTPResponse:
            ...
        # url_func is registered View object

        This also accept View objects so that the decorator can be stacked
        multiple times to allow multiple URLs into the same function.
        """

        def _router_inner(view_or_func: ViewOrFunc) -> View:
            return self.register(pattern, view_or_func)

        return _router_inner

    def _wsgi_app(self, environ: dict[str, typing.Any],
        start_response: StartFunc) -> HTTPResponseData:
        """
        WSGI entry point. Parses the request, dispatches it, and returns the
        response.
        """

        request: HTTPRequest | None = None

        try:
            request = HTTPRequest.from_req(environ)
            response = self.dispatch(request)
        except HTTPBaseError as err:
            response = err.response
        finally:
            if request is not None:
                request.drain_request_body()

        headers = [
            ('Content-Type', response.content_type),
            ('Cache-Control', 'no-cache'),
        ]

        for key, value in response.extra_headers.items():
            headers.append((key, value))

        content_length, data = response.get_data()
        if content_length >= 0:
            headers.append(('Content-Length', str(content_length)))

        status = f"{response.status.value} {response.status.phrase}"

        start_response(status, headers)
        return data

route = Router()
