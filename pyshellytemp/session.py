"""
User and session management
"""

from email.utils import formatdate
import dataclasses
import datetime
import enum
import hashlib
import hmac
import os
import secrets
import typing

from .db import DBObject, unique
from .util import render
from .web import route, HTTPRequest, HTTPResponse, redirect, ReqExtData, View


# Maximum number of simultaneous sessions per user
MAX_SESSIONS = 10

# Delay between last session use refreshes
SESSION_REFRESH_DELAY = datetime.timedelta(seconds=300)

# Session cookie name
SESSION_COOKIE_NAME = 'sessid'

# Max time between session uses before it is discarded
SESSION_EXPIRY_DELAY = datetime.timedelta(days=30)


class User(DBObject, table='users'):
    """
    Represents a user that can log in on the site to perform administrative
    tasks.
    """

    username: str = unique()
    passwd_hash: str

    def set_password(self, password: str) -> None:
        """
        Change the user’s password.
        """

        self.passwd_hash = self._hash_password(password)
        self.save()

    @classmethod
    def create_user(cls, username: str, password: str) -> typing.Self:
        """
        Creates a new user in the database.
        """

        passwd_hash = cls._hash_password(password)

        return cls(username=username, passwd_hash=passwd_hash)

    @classmethod
    def try_login_user(cls, username: str, password: str) -> typing.Self | None:
        """
        Tries to login the specified user. Returns the user on success, None
        on failure (if the user does not exist or the password is invalid)
        """

        user = cls.get_opt(username=username)
        if user is None:
            return None

        if not cls._check_password(password, user.passwd_hash):
            return None

        return user

    @staticmethod
    def from_request(request: HTTPRequest) -> 'User':
        """
        Retrieves the user from a request’s session.
        Should only be called on views that require login
        """

        session = request.get_ext(SessionData).session
        assert session is not None
        return session.user

    @staticmethod
    def _hash_password(password: str) -> str:
        """
        Creates a hash of the password. The returned string indicates the
        used algorithm and the length.
        """

        salt = os.urandom(16)
        pwd_hash = hashlib.pbkdf2_hmac('sha512', password.encode('utf-8'), salt,
            500_000)

        return f'X${salt.hex()}${pwd_hash.hex()}'

    @staticmethod
    def _check_password(password: str, pwd_hash: str) -> bool:
        """
        Checks the password against a password hash that was previously returned
        by _hash_password.
        """

        algo, salt_hex, pwd_hash_hex = pwd_hash.split('$')
        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(pwd_hash_hex)

        if algo != 'X':
            raise AssertionError(f"Unknown algorithm {algo}")

        calc_hash = hashlib.pbkdf2_hmac('sha512', password.encode('utf-8'),
            salt, 500_000)

        return hmac.compare_digest(expected_hash, calc_hash)


class Session(DBObject, table='sessions'):
    """
    Represents an active user session.
    """

    sess_id: str = unique()
    user: User
    last_activity: datetime.datetime
    message: str

    @classmethod
    def create_for_user(cls, request: HTTPRequest, user: User) -> str:
        """
        Create session for the newly logged-in user. Returns the Set-Cookie
        data containing the session ID.
        """

        session_id = secrets.token_urlsafe(32)
        now = datetime.datetime.now()
        expiry = now + SESSION_EXPIRY_DELAY

        cookie_val = cls._format_cookie(request, session_id, expiry)

        cls(sess_id=session_id, user=user, last_activity=now, message='')

        # Remove old sessions
        sessions = cls.get_all(user=user).order_by('-last_activity')
        sessions[MAX_SESSIONS:].delete()

        return cookie_val

    @classmethod
    def get_session_reset_cookie(cls, request: HTTPRequest) -> str:
        """
        Formats a session reset cookie for the given request.
        """

        expiry = datetime.datetime.fromtimestamp(0)

        return cls._format_cookie(request, '', expiry)

    def refresh_if_needed(self, request: HTTPRequest) -> str | None:
        """
        Refresh the last-use time of a session if needed.
        Returns a session refresh Set-Cookie if the cookie needs to be
        updated.
        """

        now = datetime.datetime.now()
        expiry = now + SESSION_EXPIRY_DELAY

        if (now - self.last_activity) <= SESSION_REFRESH_DELAY:
            return None

        self.last_activity = now
        self.save()

        return self._format_cookie(request, self.sess_id, expiry)

    @staticmethod
    def _format_cookie(request: HTTPRequest, session_id: str,
        expiry: datetime.datetime) -> str:
        """
        Returns a Set-Cookie string that sets or resets the session cookie.
        To reset the cookie, use an empty session ID and an expiry in the past.
        """

        prefix = request.prefix

        expiry_str = formatdate(expiry.timestamp(), usegmt=True)

        cookie_data = [
            f'{SESSION_COOKIE_NAME}={session_id}',
            f'Domain={prefix.server_host}',
            f'Path={prefix.path}/',
            f'Expires={expiry_str}',
            'SameSite=Strict',
            'HttpOnly',
        ]

        if prefix.protocol == 'https':
            cookie_data.append('Secure')

        return '; '.join(cookie_data)


class ViewSessionType(enum.Enum):
    """
    Defines how the session manager handles a called view.
    """

    NONE = 'none'  # Do not resolve the session on this view
    DEFAULT = 'default'  # Resolve the session
    AUTHENTICATE = 'auth'  # Resolve the session, ask for login if no session


@dataclasses.dataclass(frozen=True)
class SessionData(ReqExtData):
    """
    Session request extension data
    """

    session: Session | None
    message: str

    def set_next_message(self, message: str) -> None:
        """
        Stores a message in the session. The message will be placed in the
        session data of the next page being loaded. This is used to transmit
        a message during a POST -> GET redirect.
        """

        session = self.session

        if session is None:
            raise AssertionError("No session active")

        session.message = message
        session.save()

    def put_into_context(self, context: dict[str, typing.Any]) -> None:
        """
        If the session is valid, put the logged user in the context.
        """

        if self.session is not None:
            context['user'] = self.session.user
        else:
            context['user'] = None


@route.request_extension
def _session_request_extension(request: HTTPRequest,
    view: View, extra_headers: dict[str, str]) -> HTTPResponse | None:
    """
    Function registered to be called on each request. Validates the active
    session and puts in the request extension data.
    If the no_session decorator is used on the view, this function does nothing.
    If the login_required decorator is used and no session is active, the
    request will be redirected to the login page.
    """

    sess_type = view.get_prop(ViewSessionType, ViewSessionType.DEFAULT)

    if sess_type is ViewSessionType.NONE:
        return None

    session_id = request.get_cookies().get(SESSION_COOKIE_NAME)
    session = None
    if session_id is not None:
        session = Session.get_opt(sess_id=session_id)

    if session is None:
        if sess_type is ViewSessionType.AUTHENTICATE:
            return redirect(request, f'/login?next={request.path}',
                permanent=False)
        message = ''
    else:
        set_cookie = session.refresh_if_needed(request)
        if set_cookie:
            extra_headers['Set-Cookie'] = set_cookie

        message = session.message
        if message:
            session.message = ''
            session.save()

    data = SessionData(session, message)

    request.set_ext(data)

    # Continue normal processing
    return None


def login_required(view: View) -> View:
    """
    Decorator put on a View that requires login.
    """

    if not isinstance(view, View):
        raise ValueError("Put the login_required decorator on the top of the "
            "route decorator")

    view.set_prop(ViewSessionType.AUTHENTICATE)

    return view


def no_session(view: View) -> View:
    """
    Decorator that removes the use of sessions for the specified view.
    """

    if not isinstance(view, View):
        raise ValueError("Put the no_session decorator on the top of the "
            "route decorator")

    view.set_prop(ViewSessionType.NONE)

    return view


@route('/login')
def login(request: HTTPRequest) -> HTTPResponse:
    """
    Login view.
    """

    next_path = request.query.get('next', '/')
    username = ''
    message = ''

    if not route.is_valid_path(next_path) or next_path in {'/login', '/logout'}:
        next_path = '/'

    cur_session = request.get_ext(SessionData).session
    if cur_session is not None:
        return redirect(request, next_path, permanent=False)

    if request.post is not None:
        data = request.post.get_form_data()
        username = data.get('username', '')
        password = data.get('password', '')

        user = User.try_login_user(username, password)
        if user is None:
            message = 'Invalid username or password.'
        else:
            session_cookie = Session.create_for_user(request, user)
            return redirect(request, next_path, permanent=False,
                extra_headers={'Set-Cookie': session_cookie})

    ctx = {
        'username': username,
        'message': message,
    }

    return render(request, 'session/login.html', ctx)


@route('/logout')
def logout(request: HTTPRequest) -> HTTPResponse:
    """
    Logout view.
    """

    cur_session = request.get_ext(SessionData).session
    if cur_session is not None:
        if request.post is None:
            return render(request, 'session/logout.html')
        cur_session.delete()

    reset_cookie = Session.get_session_reset_cookie(request)

    return redirect(request, '/', permanent=False,
        extra_headers={'Set-Cookie': reset_cookie})
