"""
Application code and WSGI entry point
"""

from http import HTTPStatus
import dataclasses
import datetime
import ipaddress
import json
import logging
import pathlib
import re
import threading
import typing

from .models import Settings, Device
from .report_processor import ReportProcessor, DEV_IDENTIFY_IDENT
from .session import no_session, login_required, SessionData, User
from .util import render, join_lines, float_or_default
from .web import route, redirect_to_view, HTTPRequest, HTTPResponse, view_path
from .web import HTTPFileResponse, HTTPTextResponse, HTTPBaseError

# Delay before considering sensor info obsolete
OBSOLETE_INTERVAL = datetime.timedelta(hours=24)

STATIC_DIR = pathlib.Path(__file__).parent / 'static'
LOGGER = logging.getLogger(__name__)


@route('/')
def main(request: HTTPRequest) -> HTTPResponse:
    """
    Main view function
    """

    limit = datetime.datetime.now() - OBSOLETE_INTERVAL

    ctx = {
        'devices': Device.get_all().order_by('name'),
        'limit': limit,
    }

    return render(request, 'app/index.html', ctx)


@login_required
@route('/settings/')
def settings_view(request: HTTPRequest) -> HTTPResponse:
    """
    Settings view
    """

    settings = Settings.get()
    cur_user = User.from_request(request)

    if request.post is not None:
        data = request.post.get_form_data()
        if 'enable_disco' in data:
            settings.set_discovery(enabled=True)

        if 'disable_disco' in data:
            settings.set_discovery(enabled=False)

        if 'set_values' in data:
            settings.dev_username = data.get('device_uname', '')
            settings.dev_password = data.get('device_pass', '')
            settings.save()

        return redirect_to_view(request, settings_view)

    ctx = {
        'device_uname': settings.dev_username,
        'device_pass': settings.dev_password,
        'disco_remaining': settings.discovery_remaining,
        'device_info': (
            (
                device,
                view_path(request, device_edit, device_id=device.ident),
                view_path(request, device_delete, device_id=device.ident),
            )
            for device in Device.get_all().order_by('ident')
        ),
        'user_info': (
            (
                user,
                user.id == cur_user.id,
                view_path(request, user_edit, username=user.username),
                view_path(request, user_delete, username=user.username),
            )
            for user in User.get_all().order_by('username')
        ),
    }

    return render(request, 'app/settings.html', ctx)


@dataclasses.dataclass
class DeviceFormData:
    """
    Handle a device configuration settings.
    """

    device: Device
    name: str
    temp_thresh: str
    hum_thresh: str
    temp_off: str
    hum_off: str

    def validate(self, data: dict[str, str]) -> tuple[list[str], bool]:
        """
        Validate POST data and update the form values in the device object.
        Return a list of validation error messages and a boolean indicating that
        device configuration is needed. If the list of messages is empty, the
        device can be saved.
        """

        errors: list[str] = []
        need_config_set = False

        device = self.device

        # Update form values
        self.name = data.get('dev_name', '').strip()
        self.temp_thresh = data.get('temp_thresh', '').strip()
        self.hum_thresh = data.get('hum_thresh', '').strip()
        self.temp_off = data.get('temp_off', '').strip()
        self.hum_off = data.get('hum_off', '').strip()

        # Determine “cleaned” values
        name = self.name
        if not name:
            errors.append("Device name cannot be empty.")

        temp_thresh = float_or_default(self.temp_thresh, default=-1)
        if not 0 <= temp_thresh <= 20:
            errors.append("Temperature threshold should be between 0 and "
                "15 °C.")
        elif temp_thresh != device.temp_thresh:
            need_config_set = True

        hum_thresh = float_or_default(self.hum_thresh, default=-1)
        if not 0 <= hum_thresh <= 100:
            errors.append("Humidity threshold should be between 0 and "
                "100.")
        elif hum_thresh != device.hum_thresh:
            need_config_set = True

        temp_off = float_or_default(self.temp_off, default=-200)
        if not -50 <= temp_off <= 50:
            errors.append("Temperature offset should be between -50 and "
                "+50 °C.")
        elif temp_off != device.temp_off:
            need_config_set = True

        hum_off = float_or_default(self.hum_off, default=-1)
        if not -50 <= hum_off <= 50:
            errors.append("Humidity offset should be between -50 and "
                "50.")
        elif hum_off != device.hum_off:
            need_config_set = True

        if not errors:
            device.name = name
            device.temp_thresh = temp_thresh
            device.hum_thresh = hum_thresh
            device.temp_off = temp_off
            device.hum_off = hum_off

            if need_config_set:
                device.need_config_set = True

        return errors, need_config_set

    @classmethod
    def from_device(cls, device: Device) -> typing.Self:
        """
        Initialize an instance from a device.
        """

        return cls(device, device.name, str(device.temp_thresh),
            str(device.hum_thresh), str(device.temp_off), str(device.hum_off))


@login_required
@route('/settings/device/{device_id}/')
def device_edit(request: HTTPRequest, device_id: str) -> HTTPResponse:
    """
    Device settings view
    """

    session_data = request.get_ext(SessionData)

    device = Device.get_opt(ident=device_id)
    if device is None:
        return HTTPTextResponse.msg_page(HTTPStatus.NOT_FOUND)

    messages: list[str] = []
    if session_data.message:
        messages.append(session_data.message)

    form = DeviceFormData.from_device(device)

    if request.post is not None:
        messages, need_config_set = form.validate(request.post.get_form_data())

        if not messages:
            device.save()

            if need_config_set:
                session_data.set_next_message("Settings updated. They will be "
                    "applied at the next device refresh.")
            else:
                session_data.set_next_message("Settings updated.")
            return redirect_to_view(request, device_edit, device_id=device_id)

    ctx = {
        'device': device,
        'form': form,
        'message': join_lines(messages),
    }

    return render(request, 'app/device.html', ctx)


@login_required
@route('/settings/device/{device_id}/delete')
def device_delete(request: HTTPRequest, device_id: str) -> HTTPResponse:
    """
    Device deletion view
    """

    device = Device.get_opt(ident=device_id)
    if device is None:
        return HTTPTextResponse.msg_page(HTTPStatus.NOT_FOUND)

    if request.post is not None:
        data = request.post.get_form_data()
        if 'confirm_yes' in data:
            device.delete()
            return redirect_to_view(request, settings_view)

        if 'confirm_no' in data:
            return redirect_to_view(request, settings_view)

    ctx = {
        'message': f"Do you really want to delete device “{device.name}”?",
        'confirm_no': "Cancel",
        'confirm_yes': "Delete",
    }

    return render(request, 'app/confirm.html', ctx)


@login_required
@route('/settings/identify')
def identify(request: HTTPRequest) -> HTTPResponse:
    """
    Device identify view
    """

    settings = Settings.get()

    if request.post is not None:
        if 'cancel' in request.post.get_form_data():
            LOGGER.info("Identify operation cancelled")
            settings.dev_identify = ''
            settings.save()
            return redirect_to_view(request, settings_view)

        until = datetime.datetime.now() + datetime.timedelta(minutes=5)
        LOGGER.info("Identify operation started (until %s)", until)
        settings.dev_identify = DEV_IDENTIFY_IDENT
        settings.identify_until = until
        settings.save()
        return redirect_to_view(request, identify)

    dev_identify = settings.dev_identify

    if dev_identify == '':
        # No identify in progress
        return redirect_to_view(request, settings_view)

    if dev_identify == DEV_IDENTIFY_IDENT:
        # Identify operation in progress

        if settings.identify_until < datetime.datetime.now():
            LOGGER.info("Identify operation timed out")
            settings.dev_identify = ''
            settings.save()
            return redirect_to_view(request, settings_view)

        headers = {
            'Refresh': '2',
        }
        return render(request, 'app/identify.html', headers=headers)

    LOGGER.info("Identify operation completed with ID %s", dev_identify)
    return redirect_to_view(request, device_edit, device_id=dev_identify)


@dataclasses.dataclass
class UserFormData:
    """
    Handle the user creation/modification form.
    """

    session_data: SessionData
    user: User | None
    username: str
    password: str
    confirm: str
    messages: list[str]

    def perform(self, data: dict[str, str]) -> bool:
        """
        Validate POST data, update the form values and perform the operation.
        Return True iff the operation succeeded.
        """

        self.messages.clear()

        # Update form values
        self.username = data.get('username', '').strip()
        self.password = data.get('password', '')
        self.confirm = data.get('confirm', '')

        # Validate values
        if not self.username:
            self.messages.append("Username cannot be blank.")

        if self.user is None and not self.password:
            self.messages.append("Password cannot be blank.")
        elif self.password != self.confirm:
            self.messages.append("Password and confirmation do not match.")

        if self.messages:
            return False

        if self.user is None:
            # Create user
            try:
                User.create_user(username=self.username, password=self.password)
            except User.AlreadyExists:
                self.messages.append("User creation failed. Another user with "
                    "the same name already exists.")
                return False

            self.session_data.set_next_message("User created.")
        else:
            try:
                self.user.username = self.username
                self.user.save()
            except User.AlreadyExists:
                self.messages.append("User renaming failed. Another user with "
                    "the same name already exists.")
                return False

            if self.password:
                self.user.set_password(self.password)
            self.session_data.set_next_message("User modified.")

        return True

    @classmethod
    def from_user(cls, request: HTTPRequest,
        user: User | None) -> typing.Self:
        """
        Initialize an instance from a request and a user (None for creation)
        """

        session_data = request.get_ext(SessionData)
        username = user.username if user else ''

        if session_data.message:
            messages = [session_data.message]
        else:
            messages = []

        return cls(session_data, user, username, '', '', messages)


@login_required
@route('/settings/user/new')
@route('/settings/user/{username}/')
def user_edit(request: HTTPRequest,
    username: str | None = None) -> HTTPResponse:
    """
    User creation/edit view
    """

    if username is None:
        user: User | None = None
    else:
        user = User.get_opt(username=username)

        if user is None:
            return HTTPTextResponse.msg_page(HTTPStatus.NOT_FOUND)

    form = UserFormData.from_user(request, user)

    if request.post is not None:
        if form.perform(request.post.get_form_data()):
            return redirect_to_view(request, user_edit, username=form.username)

    ctx = {
        'form': form,
        'message': join_lines(form.messages),
    }

    return render(request, 'app/user_edit.html', ctx)


@login_required
@route('/settings/user/{username}/delete')
def user_delete(request: HTTPRequest,
    username: str | None = None) -> HTTPResponse:
    """
    User deletion view
    """

    cur_user = User.from_request(request)
    user = User.get_opt(username=username)
    if user is None or user.id == cur_user.id:
        return HTTPTextResponse.msg_page(HTTPStatus.NOT_FOUND)

    if request.post is not None:
        data = request.post.get_form_data()
        if 'confirm_yes' in data:
            user.delete()
            return redirect_to_view(request, settings_view)

        if 'confirm_no' in data:
            return redirect_to_view(request, settings_view)

    ctx = {
        'message': f"Do you really want to delete user “{user.username}”?",
        'confirm_no': "Cancel",
        'confirm_yes': "Delete",
    }

    return render(request, 'app/confirm.html', ctx)


@no_session
@route.to_class_method('/report')
class ReportHandler:
    """
    Handles a report from a Shelly H&T device.
    Validates the request and performs the necessary tasks.
    """

    IDENT_RE = re.compile(r'^shellyht-([0-9A-F]{6})$')

    _processor: typing.ClassVar[typing.Optional['ReportProcessor']] = None
    _processor_lock: typing.ClassVar = threading.Lock()

    def __init__(self, request: HTTPRequest):
        self._request = request
        self._req_date = datetime.datetime.now()

    def process(self) -> HTTPResponse:
        """
        Process the report and returns the response.
        """

        request = self._request
        req_date = self._req_date
        addr = self._validate_remote_addr(request.remote_addr)

        hum, temp, ident = self._parse_request(request)
        info = ReportProcessor.Info(temp, hum, req_date)

        # Processing the report may entail sending a HTTP request to the device,
        # and it’s not sure that it may process a request while waiting for
        # a response, so we send the response and let the report be processed
        # asynchronously.
        self._process_report(ident, addr, info)

        return HTTPTextResponse('OK\n', content_type='text/plain;charset=utf-8')

    @classmethod
    def _process_report(cls, ident: str, addr: str,
        info: ReportProcessor.Info) -> None:
        """
        Start the asynchronous processing of a report.
        """

        with cls._processor_lock:
            if cls._processor is None:
                cls._processor = ReportProcessor()

        cls._processor.process_report_async(ident, addr, info)

    @classmethod
    def _validate_remote_addr(cls, addr: str) -> str:
        """
        Validates that the report request comes from a local IPv4 address.
        Converts the IPv4 to its canonical form if it’s embedded in IPv6.
        """

        ip_addr = ipaddress.ip_address(addr)
        if isinstance(ip_addr, ipaddress.IPv6Address) and ip_addr.ipv4_mapped:
            ipv4_addr = ip_addr.ipv4_mapped
        elif isinstance(ip_addr, ipaddress.IPv4Address):
            ipv4_addr = ip_addr
        else:
            LOGGER.warning("Rejecting report request from %s (not IPv4)",
                addr)
            raise cls.Error(HTTPStatus.FORBIDDEN, "Forbidden") from None

        if not ipv4_addr.is_private:
            LOGGER.warning("Rejecting report request from %s (not local)",
                addr)
            raise cls.Error(HTTPStatus.FORBIDDEN, "Forbidden")

        return ipv4_addr.exploded

    @classmethod
    def _parse_request(cls, request: HTTPRequest) -> tuple[float, float, str]:
        """
        Parse the request’s query string and reports the provided temperature,
        humidity, and device identifier.
        Raises an HTTPError if the request is invalid.
        """

        query = request.query

        if request.post is not None:
            LOGGER.warning("Received invalid report from %s: POST data %r",
                request.remote_addr, request.post)
            raise cls.Error(HTTPStatus.METHOD_NOT_ALLOWED,
                "Method not allowed")

        try:
            hum = float(query['hum'])
        except (KeyError, ValueError):
            hum = None

        try:
            temp = float(query['temp'])
        except (KeyError, ValueError):
            temp = None

        ident_match = cls.IDENT_RE.match(query.get('id', ''))

        if hum is None or temp is None or ident_match is None:
            LOGGER.warning("Received invalid report from %s: query %r",
                request.remote_addr, query)
            raise cls.Error(HTTPStatus.BAD_REQUEST, "Invalid or missing "
                "request parameters\n")

        return hum, temp, ident_match.group(1)

    @classmethod
    def handle_request(cls, request: HTTPRequest) -> HTTPResponse:
        """
        View function that handles the report request
        """

        return cls(request).process()

    class Error(HTTPBaseError):
        """
        Error raised when the request is invalid. Formatted into an HTTPResponse
        that will be transmitted to the client.
        """

        def __init__(self, status: HTTPStatus, message: str):
            response = HTTPTextResponse(message + "\n", status,
                content_type='text/plain;charset=utf-8')

            super().__init__(response)


@no_session
@route('/autoconf')
def autoconf(request: HTTPRequest) -> HTTPResponse:
    """
    Used by the shelly_config script to perform autoconfiguration.
    """

    if request.post is None:
        return HTTPTextResponse.msg_page(HTTPStatus.NOT_FOUND)

    data = request.post.get_form_data()
    try:
        username = data['username']
        password = data['password']
    except KeyError:
        return HTTPTextResponse.msg_page(HTTPStatus.BAD_REQUEST)

    if User.try_login_user(username, password) is None:
        return HTTPTextResponse.msg_page(HTTPStatus.FORBIDDEN)

    settings = Settings.get()

    if 'discovery' in data:
        settings.set_discovery(enabled=True)

    json_data = json.dumps({
        'dev_username': settings.dev_username,
        'dev_password': settings.dev_password,
    })

    return HTTPTextResponse(json_data + "\n", content_type='application/json')


@no_session
@route('/static/{path}')
def static(_request: HTTPRequest, path: str) -> HTTPResponse:
    """
    Routes /static/ URLs to the static directory
    """

    return HTTPFileResponse.serve_file(STATIC_DIR, path)


application = route.get_wsgi_app()
