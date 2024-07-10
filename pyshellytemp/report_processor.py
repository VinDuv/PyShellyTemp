"""
Shelly H&T report processor
"""

from http.client import HTTPConnection, HTTPResponse as HTTPClientResponse
from urllib.parse import urlencode
import atexit
import base64
import datetime
import json
import logging
import queue
import re
import threading
import typing

from .models import Settings, Device, Report


# Delay between two refreshes of the device status (battery level, etc)
REFRESH_INTERVAL = datetime.timedelta(hours=12)

# Indicates that a device identify operation is in progress
DEV_IDENTIFY_IDENT = 'search'

LOGGER = logging.getLogger(__name__)


ReportData: typing.TypeAlias = tuple[str, str, 'ReportProcessor.Info']
QueryDict: typing.TypeAlias = dict[str, str | int | float]
QueryRes: typing.TypeAlias = tuple[str, Device.Status, bool]


class ReportProcessor:
    """
    Asynchronously processes a Shelly H&T report.
    """

    MAC_RE = re.compile(r'^[0-9A-F]{12}$')

    _queue: queue.SimpleQueue[ReportData | None]
    _thread: threading.Thread

    class Info(typing.NamedTuple):
        """
        Report input information (decoded from the Shelly request)
        """

        temp: float
        hum: float
        req_date: datetime.datetime

    def __init__(self) -> None:
        LOGGER.info("Initializing report processor")
        self._queue = queue.SimpleQueue()
        self._closed = False
        self._thread = threading.Thread(name='ShellyReportProcessor',
            target=self._thread_proc, args=(self._queue,), daemon=True)
        self._thread.start()
        atexit.register(self.close)

    def process_report_async(self, ident: str, addr: str, info: Info) -> None:
        """
        Asynchronously processes a report.
        """

        if self._closed:
            LOGGER.warning("Received report after closing")
            return

        self._queue.put((ident, addr, info))

    def close(self) -> None:
        """
        Finishes processes any pending requests and exits the process thread.
        """

        LOGGER.info("Closing report processor")
        # Note that this is safe to be called multiple times
        self._queue.put(None)
        self._thread.join(timeout=60)

    @classmethod
    def _thread_proc(cls,
        req_queue: queue.SimpleQueue[ReportData | None]) -> None:
        """
        Processes requests put in the queue.
        """

        # Devices that need an update of their update status
        update_status: list[Device] = []

        LOGGER.debug("Processor thread procedure started")
        while True:
            if update_status:
                # Need to fetch update status from a device, do it after 30
                # seconds of idle
                timeout = 30
            else:
                timeout = None

            try:
                item = req_queue.get(timeout=timeout)
            except queue.Empty:
                # Timeout, perform update status fetch for all devices
                settings = Settings.get()
                for device in update_status:
                    cls._fetch_dev_update_status(device, settings)
                update_status.clear()
                continue

            if item is None:
                LOGGER.debug("Processor thread procedure exiting")
                return

            ident, addr, info = item

            cls._process_report(ident, addr, info, update_status)

    @classmethod
    def _process_report(cls, ident: str, addr: str, info: Info,
        update_status: list[Device]) -> None:
        """
        Processes a report (on a background thread).
        """

        device = Device.get_opt(ident=ident)
        req_date = info.req_date

        if device is None:
            LOGGER.debug("Processing unregistered device %s (IP %s)",
                ident, addr)
            device = cls._handle_unknown_device(addr, ident, req_date,
                update_status)
        else:
            LOGGER.debug("Processing registered device %s (IP %s)",
                device.name, addr)
            cls._handle_device(device, addr, req_date, update_status)

        if device is None:
            return

        device.last_temp = info.temp
        device.last_hum = info.hum
        device.save()

        Report(device=device, tstamp=req_date, temp=info.temp, hum=info.hum)

    @classmethod
    def _handle_unknown_device(cls, addr: str, ident: str,
        req_date: datetime.datetime, update_status: list[Device]) -> \
        Device | None:
        """
        Handles a device whose identifier is not registered in the database yet.
        Creates a Device object and completes it. If it is valid, returns it.
        If the device is invalid, returns None.
        The device returned (if any) must be saved to the database.
        """

        device = Device.new_empty()
        device.name = ident
        device.ip_addr = addr
        device.last_report = req_date
        device.last_refresh = req_date

        settings = Settings.get()
        if req_date > settings.discover_until:
            LOGGER.warning("Cannot register device %s (%s): discovery disabled",
                ident, addr)
            return None

        try:
            mac_addr, status, _ = cls._query_device_info(device, settings)
        except cls.QueryError as err:
            LOGGER.warning("Cannot register device %s (%s): %s", ident, addr,
                err)
            return None

        if mac_addr[6:] != ident:
            LOGGER.warning("Cannot register device %s (%s): identifier does "
                "not match MAC address %s", ident, addr, mac_addr)
            return None

        device.need_config_set = False
        device.ident = ident
        device.status = status
        device.update_status = 'unknown'

        LOGGER.info("Registered device %s (%s), status %s",
            ident, addr, status.name)

        # Need to fetch update status later
        update_status.append(device)

        return device

    @classmethod
    def _handle_device(cls, device: Device, addr: str,
        req_date: datetime.datetime, update_status: list[Device]) -> None:
        """
        Handles an already registered device. Updates the device info. The
        device must be saved to the database afterwards.
        """

        status = Device.Status.OK
        settings = Settings.get()
        need_refresh = False
        set_config: QueryDict | None = None

        if device.ip_addr != addr:
            LOGGER.warning("Device %s IP address changed from %s to %s",
                device.name, device.ip_addr, addr)
            device.ip_addr = addr

        if device.need_config_set:
            LOGGER.debug("Refreshing device %s and sending new settings" ,
                device.name)
            need_refresh = True
            set_config = {
                'temperature_threshold': device.temp_thresh,
                'humidity_threshold': device.hum_thresh,
                'temperature_offset': device.temp_off,
                'humidity_offset': device.hum_off,
            }

        elif (req_date - device.last_refresh) > REFRESH_INTERVAL:
            LOGGER.debug("Refreshing device %s status (outdated)" , device.name)
            need_refresh = True

        elif settings.dev_identify == DEV_IDENTIFY_IDENT:
            if settings.identify_until < datetime.datetime.now():
                LOGGER.info("Identify operation timed out")
                settings.dev_identify = ''
                settings.save()
            else:
                LOGGER.debug("Refreshing device %s status (for device "
                    "identify)", device.name)
                need_refresh = True

        if need_refresh:
            try:
                _, status, button_act = cls._query_device_info(device, settings,
                set_config)
            except cls.QueryError as err:
                LOGGER.warning("Error querying device %s (%s): %s", device.name,
                    device.ip_addr, err)
                status = err.status
            else:
                # No error
                device.last_refresh = req_date
                if set_config is not None:
                    LOGGER.info("New configuration applied on %s", device.name)
                    device.need_config_set = False

                if button_act:
                    LOGGER.info("Identify operation matched device %s",
                        device.ident)
                    settings.dev_identify = device.ident
                    settings.save()

            # Need to fetch update status later
            update_status.append(device)

        device.last_report = req_date
        device.status = status

    @classmethod
    def _query_device_info(cls, device: Device, settings: Settings,
        set_config: QueryDict | None = None) -> QueryRes:
        """
        Queries the device properties and configuration and update the device
        object.
        Can optionally set configuration parameters provided.
        Raises QueryError if the query fails.
        Return the MAC address property of the device, the device sensor status,
        and a boolean indicating if the device was woken up by its button.
        """

        conn = HTTPConnection(device.ip_addr, timeout=1)
        try:
            return cls._query_dev_info_with_conn(conn, device, settings,
                set_config)
        finally:
            conn.close()

    @classmethod
    def _query_dev_info_with_conn(cls, conn: HTTPConnection, device: Device,
        settings: Settings, set_config: QueryDict | None = None) -> QueryRes:
        """
        Queries the device properties and configuration using the provided
        HTTP connection and update the device object.
        Can optionally set configuration parameters provided.
        Return the MAC address property of the device, the device sensor status,
        and a boolean indicating if the device was woken up by its button.
        """

        headers = cls._get_auth_headers(settings)
        json_data = cls._query(conn, '/status', headers)

        device.bat_percent = cls._get_float(json_data, 'bat', 'value',
            min_v=0.0, max_v=100.0)
        device.bat_volt = cls._get_float(json_data, 'bat', 'voltage',
            min_v=0.0)

        device.mem_total = cls._get_int(json_data, 'ram_total', min_v=0)
        device.mem_free = cls._get_int(json_data, 'ram_free', min_v=0)
        device.fs_size = cls._get_int(json_data, 'fs_size', min_v=0)
        device.fs_free = cls._get_int(json_data, 'fs_free', min_v=0)

        mac_addr = cls._get_str(json_data, 'mac', pattern=cls.MAC_RE)

        if not cls._get_bool(json_data, 'is_valid'):
            status = Device.Status.DEVICE_NOT_VALID
        elif (not cls._get_bool(json_data, 'hum', 'is_valid') or
            not cls._get_bool(json_data, 'tmp', 'is_valid')):
            status = Device.Status.SENSOR_NOT_VALID
        else:
            status = Device.Status.OK

        reasons = cls._get_value(json_data, 'act_reasons')
        if not isinstance(reasons, list) or not all(isinstance(item, str) for
            item in reasons):
            raise cls.BadData(f"act_reasons: {reasons!r} is not an string list")

        button_act = 'button' in reasons

        json_data = cls._query(conn, '/settings', headers, set_config)
        device.temp_thresh = cls._get_float(json_data, 'sensors',
            'temperature_threshold', min_v=0.0)
        device.hum_thresh = cls._get_float(json_data, 'sensors',
            'humidity_threshold', min_v=0.0)
        device.temp_off = cls._get_float(json_data, 'temperature_offset',
            min_v=0.0)
        device.hum_off = cls._get_float(json_data, 'temperature_offset',
            min_v=0.0)

        return mac_addr, status, button_act

    @classmethod
    def _fetch_dev_update_status(cls, device: Device, settings: Settings) -> \
        None:
        """
        Fetches the device update status. This is done separately from the rest
        of the updates because the Shelly H&T device takes a couple seconds to
        determine its update status after waking up, but the rest should be
        updated immediately so that device identification works, for instance.
        """

        LOGGER.debug("Fetching update status of %s", device.name)

        conn = HTTPConnection(device.ip_addr, timeout=1)
        try:
            headers = cls._get_auth_headers(settings)
            json_data = cls._query(conn, '/status', headers)
            device.update_status = cls._get_str(json_data, 'update', 'status')

        except cls.QueryError as err:
            LOGGER.warning("Error querying update status of device %s: %s",
                device.name, err)
            device.update_status = '<error>'
            device.status = err.status
        finally:
            conn.close()

        device.save()

    @staticmethod
    def _get_auth_headers(settings: Settings) -> dict[str, str]:
        """
        Return the authentication headers required to perform a status fetch
        on the device.
        """

        if settings.dev_username or settings.dev_password:
            auth_str = f'{settings.dev_username}:{settings.dev_password}'
            auth_64 = base64.b64encode(auth_str.encode('utf-8')).decode('ascii')
            return {
                'Authorization': f'Basic {auth_64}',
            }

        return {}

    @classmethod
    def _query(cls, conn: HTTPConnection, path: str, headers: dict[str, str],
        query_args: QueryDict | None = None) -> typing.Any:
        """
        Queries a URL on the device and return the decoded JSON.
        Additional arguments can be added to the path as a query string.
        """

        if query_args:
            args = [(key, str(val)) for key, val in query_args.items()]
            path += '?' + urlencode(args)

        try:
            conn.request('GET', path, headers=headers)
            with conn.getresponse() as response:
                return cls._process_response(path, response)
        except OSError as err:
            raise cls.QueryError(Device.Status.NOT_RESPONDING,
                f"Error loading {path}: {err}") from err
        except ValueError as err:
            raise cls.BadData(f"Error loading {path}: {err}") from err

    @classmethod
    def _process_response(cls, path: str,
        response: HTTPClientResponse) -> typing.Any:
        """
        Process the HTTP response from the device and return the decoded JSON.
        """

        if response.status == 401:
            raise cls.QueryError(Device.Status.AUTH_ERROR, f"Error loading "
                f"{path}: Auth error")

        if response.status != 200:
            raise ValueError(f"HTTP status {response.status}")

        content_type = response.getheader('Content-Type', '')
        if content_type != 'application/json':
            raise ValueError(f"HTTP status {response.status}")

        return json.load(response)

    @classmethod
    def _get_float(cls, json_data: typing.Any, *path: str,
        min_v: float | None = None, max_v: float | None = None) -> float:
        """
        Get a float from the JSON data at the given path.
        """

        value = cls._get_value(json_data, *path)
        if isinstance(value, int):
            value = float(value)

        if not isinstance(value, float):
            raise cls.BadData(f"{'.'.join(path)!r}: {value!r} is not a float")

        if min_v is not None and value < min_v:
            raise cls.BadData(f"{'.'.join(path)!r}: OOB {value} < {min_v}")

        if max_v is not None and value > max_v:
            raise cls.BadData(f"{'.'.join(path)!r}: OOB {value} > {max_v}")

        return value

    @classmethod
    def _get_int(cls, json_data: typing.Any, *path: str,
        min_v: int | None = None, max_v: int | None = None) -> int:
        """
        Get an int from the JSON data at the given path.
        """

        value = cls._get_value(json_data, *path)
        if not isinstance(value, int):
            raise cls.BadData(f"{'.'.join(path)!r}: {value!r} is not an int")

        if min_v is not None and value < min_v:
            raise cls.BadData(f"{'.'.join(path)!r}: OOB {value} < {min_v}")

        if max_v is not None and value > max_v:
            raise cls.BadData(f"{'.'.join(path)!r}: OOB {value} > {max_v}")

        return value

    @classmethod
    def _get_str(cls, json_data: typing.Any, *path: str,
        pattern: typing.Pattern[str] | None = None) -> str:
        """
        Get a string from the JSON data at the given path.
        """

        value = cls._get_value(json_data, *path)
        if not isinstance(value, str):
            raise cls.BadData(f"{'.'.join(path)!r}: {value!r} is not a string")

        if pattern is not None and not pattern.match(value):
            raise cls.BadData(f"{'.'.join(path)!r}: {value!r} does not match "
                f"{pattern.pattern}!r")

        return value

    @classmethod
    def _get_bool(cls, json_data: typing.Any, *path: str) -> bool:
        """
        Get a boolean from the JSON data at the given path.
        """

        value = cls._get_value(json_data, *path)
        if not isinstance(value, bool):
            raise cls.BadData(f"{'.'.join(path)!r}: {value!r} is not a boolean")

        return value

    @classmethod
    def _get_value(cls, json_data: typing.Any, *path: str) -> typing.Any:
        """
        Get a value from the JSON data at the given path. The value must exist.
        """

        for item in path:
            if not isinstance(json_data, dict):
                raise cls.BadData(f"{'.'.join(path)}: {item!r} is not a "
                    f"dict") from None
            try:
                json_data = json_data[item]
            except KeyError:
                raise cls.BadData(f"{'.'.join(path)}: Missing value at "
                    f"{item!r}") from None

        return json_data

    class QueryError(Exception):
        """
        Raised when querying the device fails. The status attribute indicates
        the type of failure, as a Device.Status.
        """

        def __init__(self, status: Device.Status, message: str):
            super().__init__(message)
            self.status = status

    class BadData(QueryError):
        """
        Raised when bad data is received while querying a device.
        """

        def __init__(self, message: str):
            super().__init__(Device.Status.BAD_DATA, message)
