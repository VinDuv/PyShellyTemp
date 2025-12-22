"""
Application code and WSGI entry point
"""

from http import HTTPStatus
import datetime
import ipaddress
import json
import logging
import pathlib
import re
import threading
import typing

from .models import Device, ShellyV1HTInfo, ShellyBLUHTInfo, Report, DevIdentify
from .forms import DeviceFormData, ShellyV1HTFormData, UserFormData
from .report_processor import ReportProcessor
from .session import no_session, login_required, SessionData, User
from .util import render, join_lines
from .web import route, redirect_to_view, HTTPRequest, HTTPResponse, view_path
from .web import HTTPFileResponse, HTTPTextResponse, HTTPBaseError, HTTPError

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

    cur_user = User.from_request(request)

    ctx = {
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


@login_required
@route('/settings/device/{device_id}/')
def device_edit(request: HTTPRequest, device_id: str) -> HTTPResponse:
    """
    Device settings view
    """

    device = Device.get_opt(ident=device_id)
    if device is None:
        return HTTPTextResponse.msg_page(HTTPStatus.NOT_FOUND)

    if device.type is Device.Type.SHELLY_V1_H_T:
        return _device_edit_shelly_v1_h_t(request, device)

    if device.type is Device.Type.SHELLY_BLU_H_T:
        return _device_edit_shelly_blu_h_t(request, device)

    typing.assert_never(device.type)


@login_required
@route('/settings/device/new')
def device_new(request: HTTPRequest) -> HTTPResponse:
    """
    New device view
    """

    if request.post is not None:
        data = request.post.get_form_data()
        if 'add_v1ht' in data:
            return _device_new_v1ht(request, data)

        if 'add_bluht' in data:
            return _device_new_bluht(request, data)

    return render(request, 'app/device_new.html')


def _device_new_v1ht(request: HTTPRequest, data: dict[str, typing.Any]) -> \
    HTTPResponse:
    """
    New Shelly V1 H&T device view
    """

    message = ''

    dev_ident = data.get('dev_ident', '')
    dev_uname = data.get('dev_uname', '')
    dev_pass = data.get('dev_pass', '')

    if not re.match(r'^(?:[0-9A-Fa-f]{6}|)$', dev_ident):
        message = "Invalid device ID."

    if not message:
        if dev_ident:
            dev_name = dev_ident
        else:
            dev_ident = ShellyV1HTInfo.REG_IDENT
            dev_name = "Wait for registration"
            Device.get_all(ident=dev_ident).delete()

        try:
            dev = Device(ident=dev_ident, type=Device.Type.SHELLY_V1_H_T,
                name=dev_name)
        except Device.AlreadyExists:
            message = "A device with this identifier already exists."

    if not message:
        ShellyV1HTInfo(device=dev, username=dev_uname, password=dev_pass)

        return redirect_to_view(request, device_wait, dev_id=dev.id)

    ctx = {
        'message': message,
        'dev_ident': dev_ident,
        'dev_uname': dev_uname,
        'dev_pass': dev_pass,
    }

    return render(request, 'app/device_new.html', ctx)


def _device_new_bluht(request: HTTPRequest, data: dict[str, typing.Any]) -> \
    HTTPResponse:
    """
    New Shelly BLU H&T device view
    """

    message = ''

    enc_key = data.get('enc_key', '')

    formatted_key = ShellyBLUHTInfo.validate_enc_key(enc_key)
    if formatted_key is None:
        message = ("Invalid encryption key (must be a hex string of 32 "
            "characters)")
    else:
        dev_ident = ShellyBLUHTInfo.REG_IDENT
        dev_name = "Wait for registration"
        Device.get_all(ident=dev_ident).delete()
        dev = Device(ident=dev_ident, type=Device.Type.SHELLY_BLU_H_T,
            name=dev_name)
        ShellyBLUHTInfo(device=dev, enc_key=formatted_key)

        return redirect_to_view(request, device_wait, dev_id=dev.id)

    ctx = {
        'message': message,
        'enc_key': enc_key,
    }

    return render(request, 'app/device_new.html', ctx)


@login_required
@route('/settings/device/wait/{dev_id:d}')
def device_wait(request: HTTPRequest, dev_id: int) -> HTTPResponse:
    """
    Wait for a newly added Shelly v1 H&T to contact the server for the first
    time.
    The new device is identified by numerical identifier instead of string
    identifier because the string identifier may change during the registration.
    """

    device = Device.get_opt(id=dev_id)
    if device is None:
        return HTTPTextResponse.msg_page(HTTPStatus.NOT_FOUND)

    if device.type is Device.Type.SHELLY_V1_H_T:
        info = ShellyV1HTInfo.get_one(device=device)
        done = info.last_refresh is not None
        tpl_file = 'app/device_new_wait_v1_h_t.html'
    elif device.type is Device.Type.SHELLY_BLU_H_T:
        done = device.status is not Device.Status.NOT_RESPONDING
        tpl_file = 'app/device_new_wait_blu_h_t.html'
    else:
        typing.assert_never(device.type)

    if done:
        return redirect_to_view(request, device_edit, device_id=device.ident)

    if request.post is not None:
        if 'cancel' in request.post.get_form_data():
            device.delete()
            return redirect_to_view(request, settings_view)

    headers = {
        'Refresh': '5',
    }

    return render(request, tpl_file, headers=headers)


def _device_edit_shelly_v1_h_t(request: HTTPRequest, device: Device) -> \
    HTTPResponse:
    """
    Settings view for Shelly v1 H&T devices
    """

    session_data = request.get_ext(SessionData)

    messages: list[str] = []
    if session_data.message:
        messages.append(session_data.message)

    info = ShellyV1HTInfo.get_one(device=device)
    form = ShellyV1HTFormData.from_info(device, info)

    if info.last_refresh is None:
        return redirect_to_view(request, device_wait, dev_id=device.id)

    if request.post is not None:
        messages, need_config_set = form.validate(request.post.get_form_data())

        if not messages:
            device.save()
            info.save()

            if need_config_set:
                session_data.set_next_message("Settings updated. They will be "
                    "applied at the next device refresh.")
            else:
                session_data.set_next_message("Settings updated.")
            return redirect_to_view(request, device_edit,
                device_id=device.ident)

    ctx = {
        'device': device,
        'info': info,
        'form': form,
        'message': join_lines(messages),
    }

    form.common.add_to_context(ctx)

    return render(request, 'app/device_shelly_v1_h_t.html', ctx)


def _device_edit_shelly_blu_h_t(request: HTTPRequest, device: Device) -> \
    HTTPResponse:
    """
    Settings view for Shelly BLU H&T devices
    """

    session_data = request.get_ext(SessionData)

    messages: list[str] = []
    if session_data.message:
        messages.append(session_data.message)

    info = ShellyBLUHTInfo.get_one(device=device)
    enc_key = info.enc_key

    if device.status is Device.Status.NOT_RESPONDING:
        return redirect_to_view(request, device_wait, dev_id=device.id)

    if request.post is not None:
        data = request.post.get_form_data()
        enc_key = data.get('enc_key', enc_key)
        common = DeviceFormData.from_dict(data)

        formatted_key = ShellyBLUHTInfo.validate_enc_key(enc_key)
        if formatted_key is None:
            messages.append("Invalid encryption key (must be a hex string of 32 "
                "characters)")

        common.validate(messages)

        if not messages:
            common.update_device(device)
            info.enc_key = enc_key
            device.save()
            info.save()

            session_data.set_next_message("Settings updated.")
            return redirect_to_view(request, device_edit,
                device_id=device.ident)

    else:
        common = DeviceFormData.from_device(device)

    ctx = {
        'device': device,
        'info': info,
        'enc_key': enc_key,
        'message': join_lines(messages),
    }

    common.add_to_context(ctx)

    return render(request, 'app/device_shelly_blu_h_t.html', ctx)


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

    if request.post is not None:
        if 'cancel' in request.post.get_form_data():
            DevIdentify.cancel_identification()
            return redirect_to_view(request, settings_view)

        DevIdentify.start_identification()
        return redirect_to_view(request, identify)

    identified = DevIdentify.get_identified_device()

    if identified == '':
        # No identify in progress
        return redirect_to_view(request, settings_view)

    if identified is None:
        # Identify operation in progress

        headers = {
            'Refresh': '2',
        }
        return render(request, 'app/identify.html', headers=headers)

    LOGGER.info("Identify operation completed with ID %s", identified)
    return redirect_to_view(request, device_edit, device_id=identified)


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


class DevData(typing.TypedDict):
    """
    Dictionary containing data from a device, to be JSON serialized.
    """

    name: str
    tempColor: str
    humColor: str
    tstamps: list[int]
    temps: list[float]
    hums: list[int]


@route('/data')
def data_view(request: HTTPRequest) -> HTTPResponse:
    """
    Returns sensor data for graph building
    """

    try:
        start = datetime.datetime.fromtimestamp(int(request.query['start']),
            datetime.timezone.utc)
        end = datetime.datetime.fromtimestamp(int(request.query['end']),
            datetime.timezone.utc)
    except (KeyError, ValueError, OverflowError):
        start = None
        end = None

    if start is None or end is None or end < start:
        raise HTTPError(HTTPStatus.BAD_REQUEST, "Missing or invalid query "
            "parameters")

    dev_results: dict[int, DevData] = {
        device.id: {
            'name': device.name,
            'tempColor': device.temp_color,
            'humColor': device.hum_color,
            'tstamps': [],
            'temps': [],
            'hums': [],
        }
        for device in Device.get_all().order_by('name')
    }

    query = Report.get_all(tstamp__gte=start, tstamp__lte=end)
    query = query.order_by('+tstamp')
    res = query.get_raw_fields('device_id', 'tstamp', 'temp', 'hum')
    for dev_id, tstamp, temp, hum in res:
        dev_data = dev_results[dev_id]
        dev_data['tstamps'].append(int(tstamp))
        dev_data['temps'].append(temp)
        dev_data['hums'].append(int(hum))

    results = list(dev_results.values())

    str_results = json.dumps(results, separators=(',', ':')) + '\n'

    return HTTPTextResponse(str_results, content_type='application/json')


class SensorValues(typing.TypedDict):
    """ Sensor values and limit for reporting """
    cur: float | None
    min: float | None
    max: float | None


class SensorData(typing.TypedDict):
    """ Data for a temperature/humidity sensor for reporting """
    name: str
    temp: SensorValues
    hum: SensorValues


@no_session
@route('/sensors')
def sensor_data(_request: HTTPRequest) -> HTTPResponse:
    """
    Returns the current sensor data as JSON. Values may be null if the sensor
    if offline.
    """

    sensors: dict[int, SensorData] = {}

    cur_time = datetime.datetime.now()
    valid_limit = cur_time - OBSOLETE_INTERVAL
    min_max_limit = cur_time - datetime.timedelta(hours=24)

    for device in Device.get_all().order_by('name'):
        if device.last_report >= valid_limit:
            temp = device.last_temp
            hum = device.last_hum
        else:
            temp = None
            hum = None

        sensors[device.id] = {
            'name': device.name,
            'temp': {
                'cur': temp,
                'min': None,
                'max': None,
            },
            'hum': {
                'cur': hum,
                'min': None,
                'max': None,
            }
        }

    min_max_query = Report.get_all(tstamp__gte=min_max_limit)
    min_max_items = min_max_query.get_raw_fields('device_id', 'min(temp)',
        'max(temp)', 'min(hum)', 'max(hum)', group_by='device_id')

    for dev_id, min_temp, max_temp, min_hum, max_hum in min_max_items:
        sensor = sensors[dev_id]

        sensor_temp = sensor['temp']
        sensor_temp['min'] = min_temp
        sensor_temp['max'] = max_temp

        sensor_hum = sensor['hum']
        sensor_hum['min'] = min_hum
        sensor_hum['max'] = max_hum

    results = {
        'sensors': list(sensors.values()),
    }

    str_results = json.dumps(results, separators=(',', ':')) + '\n'

    return HTTPTextResponse(str_results, content_type='application/json')


@no_session
@route('/autoconf')
def shelly_v1_autoconf(request: HTTPRequest) -> HTTPResponse:
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

    mode = data.get('mode', '')

    if mode == 'auth':
        result: dict[str, str] = {}

    elif mode == 'reg':
        try:
            dev_ident = data['dev_ident']
            dev_uname = data['dev_uname']
            dev_passwd = data['dev_passwd']
        except KeyError:
            dev_ident = ''

        if len(dev_ident) != 6:
            return HTTPTextResponse.msg_page(HTTPStatus.BAD_REQUEST)

        try:
            dev = Device(ident=dev_ident, type=Device.Type.SHELLY_V1_H_T,
                name=dev_ident)
        except Device.AlreadyExists:
            dev = Device.get_one(ident=dev_ident)
            info = ShellyV1HTInfo.get_one(device=dev)
            info.username = dev_uname
            info.password = dev_passwd
            info.save()
        else:
            ShellyV1HTInfo(device=dev, username=dev_uname, password=dev_passwd)

        result = {}

    else:
        return HTTPTextResponse.msg_page(HTTPStatus.BAD_REQUEST)

    response = json.dumps(result) + '\n'
    return HTTPTextResponse(response, content_type='application/json')


@no_session
@route('/static/{path}')
def static(_request: HTTPRequest, path: str) -> HTTPResponse:
    """
    Routes /static/ URLs to the static directory
    """

    return HTTPFileResponse.serve_file(STATIC_DIR, path)


application = route.get_wsgi_app()
