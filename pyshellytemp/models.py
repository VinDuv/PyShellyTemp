"""
Application models
"""

import datetime
import enum
import logging
import typing

from .db import DBObject, database, field, unique, reg_db_type


LOGGER = logging.getLogger(__name__)

database.set_default_db_path('/var/lib/pyshellytemp/db.sqlite3')
database.set_db_version(4)


class DevIdentify(DBObject, table='dev_identify'):
    """
    Device identification feature. Use a single table row to store the status.
    """

    SEARCH_IDENT: typing.ClassVar[str] = 'search'

    # Device that was identified. Set to SEARCH_IDENT when searching.
    identify: str
    until: datetime.datetime

    @classmethod
    def get_identified_device(cls) -> str | None:
        """
        Get the identifier of the identified device.
        Returns an empty string iff no identification is in progress.
        Returns None if identification is in progress.
        """

        status = cls._get()

        if status.identify == cls.SEARCH_IDENT:
            if status.until < datetime.datetime.now():
                LOGGER.info("Identify operation timed out")
                status.identify = ''
                status.save()
                return ''

            return None

        return status.identify

    @classmethod
    def device_identified(cls, ident: str) -> None:
        """
        Should be called when a device button is pressed, with the device
        identifier. If the device identification operation is in progress,
        this will complete it.
        """

        status = cls._get()

        if status.identify != cls.SEARCH_IDENT:
            return

        LOGGER.info("Identify operation matched device %s", ident)
        status.identify = ident
        status.save()

    @classmethod
    def start_identification(cls) -> None:
        """
        Start the identification operation
        """

        until = datetime.datetime.now() + datetime.timedelta(minutes=5)

        LOGGER.info("Identify operation started (until %s)", until)

        status = cls._get()
        status.identify = cls.SEARCH_IDENT
        status.until = until
        status.save()

    @classmethod
    def cancel_identification(cls) -> None:
        """
        Cancel an identification operation that is running.
        """

        LOGGER.info("Identify operation cancelled")

        status = cls._get()
        status.identify = ''
        status.save()

    @classmethod
    def _get(cls) -> typing.Self:
        """
        Returns the identify status, creating it if needed.
        """

        try:
            return cls.get_one()
        except KeyError:
            status = cls.new_empty()
            status.id = 1
            status.until = datetime.datetime(2000, 1, 1)
            status.save()

            return status


class Device(DBObject, table='devices'):
    """
    Represents a Shelly H&T device.
    """

    @reg_db_type(int)
    @enum.verify(enum.UNIQUE, enum.CONTINUOUS)
    class Status(enum.Enum):
        """
        Device status
        """

        OK = 0
        SENSOR_NOT_VALID = 1
        DEVICE_NOT_VALID = 2
        BAD_DATA = 3
        AUTH_ERROR = 4
        NOT_RESPONDING = 5

        @staticmethod
        def py_to_db(py_val: 'Device.Status') -> int:
            """
            Converts a status value into a database value
            """

            return py_val.value

        @classmethod
        def db_to_py(cls, db_val: int) -> 'Device.Status':
            """
            Converts a database value into a status value
            """

            return cls(db_val)

        def __str__(self) -> str:
            if self is self.__class__.OK:
                return self.name

            descr = self.name.replace('_', ' ')
            return descr[0] + descr[1:].lower()

    @reg_db_type(str)
    @enum.verify(enum.UNIQUE)
    class Type(enum.Enum):
        """
        Device type
        """

        SHELLY_V1_H_T = 'shv1ht'
        SHELLY_BLU_H_T = 'bluht'

        @staticmethod
        def py_to_db(py_val: 'Device.Type') -> str:
            "Converts a type value into a database value"
            return py_val.value

        @classmethod
        def db_to_py(cls, db_val: str) -> 'Device.Type':
            "Converts a database value into a status value"
            return cls(db_val)

        def __str__(self) -> str:
            if self is Device.Type.SHELLY_V1_H_T:
                return "Shelly v1 H&T"

            if self is Device.Type.SHELLY_BLU_H_T:
                return "Shelly BLU H&T"

            typing.assert_never(self)

    # Device identifier (expected to be unique across device types)
    ident: str = unique()

    # Device type
    type: Type

    # Device name
    name: str

    # Current device status
    status: Status = Status.NOT_RESPONDING

    # Last temperature received from the device (None if never received)
    last_temp: typing.Optional[float] = None

    # Last humidity received from the device (None if never received)
    last_hum: typing.Optional[float] = None

    # Last report time (set to current time at initial setup)
    last_report: datetime.datetime = field(default_factory=datetime.datetime.\
        now)

    # Battery percentage
    bat_percent: float = 0.0

    @property
    def temp(self) -> str:
        "Human-readable temperature"
        return "—" if self.last_temp is None else f"{self.last_temp:.1f} °C"

    @property
    def hum(self) -> str:
        "Human-readable humidity"
        return "—" if self.last_hum is None else f"{self.last_hum:.0f} %"

    @property
    def last_report_disp(self) -> str:
        "Human-readable last report date"
        return self.last_report.strftime('%d/%m/%Y %H:%M:%S')

    @property
    def day_report_count(self) -> int:
        "Number of reports in the last 24 hours"

        cutoff = datetime.datetime.now() - datetime.timedelta(hours=24)
        return Report.get_all(device=self, tstamp__gte=cutoff).count()

    @staticmethod
    def rssi_to_str(rssi: int) -> str:
        "Converts a RSSI number to a human-readable value"

        if rssi >= 0:
            return "Unknown"

        if rssi >= -30:
            strength = "Excellent"
        elif rssi >= -67:
            strength = "Very good"
        elif rssi >= -70:
            strength = "Good"
        elif rssi >= -80:
            strength = "Bad"
        else:
            strength = "Very bad"

        return f"{strength} (RSSI {rssi})"


class ShellyV1HTInfo(DBObject, table='shelly_v1_ht_info'):
    """
    Device information specific to Shelly v1 H&T devices.
    """

    # Device identifier for a device that is being registered.
    REG_IDENT: typing.ClassVar[str] = '_registering_v1_'

    # Associated device (type SHELLY_V1_H_T)
    device: Device = unique()

    # Credentials used to access the device status and change settings
    username: str
    password: str

    # Last data refresh time (None before initial contact)
    last_refresh: typing.Optional[datetime.datetime] = None

    # IP address of the device
    ip_addr: str = ''

    # Battery voltage
    bat_volt: float = 0.0

    # Update status
    update_status: str = 'unknown'

    # Wifi RSSI
    wifi_rssi: int = 0

    # Memory total and free
    mem_total: int = 1
    mem_free: int = 0

    # Filesystem size and free
    fs_size: int = 1
    fs_free: int = 0

    # Update thresholds
    temp_thresh: float = 0.0
    hum_thresh: float = 0.0

    # Sensor calibration offsets
    temp_off: float = 0.0
    hum_off: float = 0.0

    # Indicate if the device settings changed and need to be applied
    need_config_set: bool = False

    @property
    def last_refresh_disp(self) -> str:
        "Human-readable last refresh date"
        if self.last_refresh is None:
            return "Not registered yet"

        return self.last_refresh.strftime('%d/%m/%Y %H:%M:%S')

    @property
    def wifi_rssi_disp(self) -> str:
        "Human-readable Wi-Fi strength"
        return Device.rssi_to_str(self.wifi_rssi)

    @property
    def mem_usage(self) -> str:
        "Human-readable memory usage"

        if not 0 <= self.mem_free < self.mem_total:
            return "—"

        total = self.mem_total
        used = total - self.mem_free
        percent = 100 * used / total

        return f"{percent:.2f} % ({used / 1024:.2f} / {total / 1024:.2f} KiB)"

    @property
    def fs_usage(self) -> str:
        "Human-readable filesystem usage"

        if not 0 <= self.fs_free < self.fs_size:
            return "—"

        total = self.fs_size
        used = total - self.fs_free
        percent = 100 * used / total

        return f"{percent:.2f} % ({used / 1024:.1f} / {total / 1024:.1f} KiB)"


class ShellyBLUHTInfo(DBObject, table='shelly_blu_ht_info'):
    """
    Device information specific to Shelly BLU H&T devices.
    """

    # Device identifier for a device that is being registered.
    REG_IDENT: typing.ClassVar[str] = '_registering_blu_'

    # Associated device (type SHELLY_BLU_H_T)
    device: Device = unique()

    # MAC address
    mac_addr: str = 'unknown'

    # Firmware version
    fw_ver: str = 'unknown'

    # Reception RSSI
    rssi: int = 0

    # Encryption key (16 bytes, hex string, empty if disabled)
    enc_key: str = ''

    @property
    def rssi_disp(self) -> str:
        "Human-readable reception signal strength"
        return Device.rssi_to_str(self.rssi)

    @staticmethod
    def validate_enc_key(enc_key: str) -> str | None:
        """
        Verifies that the specified encryption key is valid.
        Returns the normalized encryption key if valid, None if not.
        """

        if not enc_key:
            return ""  # Empty key OK

        try:
            enc_key_bytes = bytes.fromhex(enc_key)
        except ValueError:
            return None

        if len(enc_key_bytes) != 16:
            return None

        return enc_key_bytes.hex()


class Report(DBObject, table='reports'):
    """
    Represents a temperature/humidity report at a point of time for a given
    device.
    """

    device: Device
    tstamp: datetime.datetime
    temp: float
    hum: float
