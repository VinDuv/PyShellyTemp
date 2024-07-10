"""
Application models
"""

import datetime
import enum
import math
import typing

from .db import DBObject, database, unique, reg_db_type


DISCOVERY_DELAY = datetime.timedelta(minutes=10)


database.set_default_db_path('/var/lib/pyshellytemp/db.sqlite3')


class Settings(DBObject, table='settings'):
    """
    Settings singleton instance.
    """

    # Allow devices to connect until this time
    discover_until: datetime.datetime

    # Username and password used to query devices status
    dev_username: str
    dev_password: str

    # Used for the device identification feature
    dev_identify: str
    identify_until: datetime.datetime

    def set_discovery(self, *, enabled: bool) -> None:
        """
        Enable/extend or disable device connection
        """

        if enabled:
            now = datetime.datetime.now(datetime.timezone.utc)
            self.discover_until = now + DISCOVERY_DELAY
        else:
            past = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
            self.discover_until = past

        self.save()

    @property
    def discovery_remaining(self) -> int:
        """
        Returns the number of minutes remaining for discovery, or 0 if
        discovery is disabled.
        """

        remaining = self.discover_until - datetime.datetime.now()
        return max(0, math.ceil(remaining.total_seconds() / 60))

    @classmethod
    def get(cls) -> typing.Self:
        """
        Returns the current settings, creating them if needed.
        """

        try:
            return cls.get_one()
        except KeyError:
            settings = cls.new_empty()
            settings.id = 1
            settings.discover_until = datetime.datetime(2000, 1, 1)
            settings.dev_username = ''
            settings.dev_password = ''
            settings.dev_identify = ''
            settings.identify_until = datetime.datetime(2000, 1, 1)
            settings.save()

            return settings


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

    # Device identifier (six hexadecimal digits, part of the MAC address)
    ident: str = unique()

    # Device name
    name: str

    # Current device status
    status: Status

    # Last temperature received from the device
    last_temp: float

    # Last humidity received from the device
    last_hum: float

    # Last report time
    last_report: datetime.datetime

    # Last data refresh time
    last_refresh: datetime.datetime

    # IP address of the device
    ip_addr: str

    # Battery percentage
    bat_percent: float

    # Battery voltage
    bat_volt: float

    # Update status
    update_status: str

    # Memory total and free
    mem_total: int
    mem_free: int

    # Filesystem size and free
    fs_size: int
    fs_free: int

    # Update thresholds
    temp_thresh: float
    hum_thresh: float

    # Sensor calibration offsets
    temp_off: float
    hum_off: float

    # Indicate if the device settings changed and need to be applied
    need_config_set: bool

    # Display helpers
    @property
    def temp(self) -> str:
        "Human-readable temperature"
        return f"{self.last_temp:.1f} °C"

    @property
    def hum(self) -> str:
        "Human-readable humidity"
        return f"{self.last_hum:.0f} %"

    @property
    def last_report_disp(self) -> str:
        "Human-readable last report date"
        return self.last_report.strftime('%d/%m/%Y %H:%M:%S')

    @property
    def last_refresh_disp(self) -> str:
        "Human-readable last refresh date"
        return self.last_refresh.strftime('%d/%m/%Y %H:%M:%S')

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

    @property
    def day_report_count(self) -> int:
        "Number of reports in the last 24 hours"

        cutoff = datetime.datetime.now() - datetime.timedelta(hours=24)
        return Report.get_all(device=self, tstamp__gte=cutoff).count()


class Report(DBObject, table='reports'):
    """
    Represents a temperature/humidity report at a point of time for a given
    device.
    """

    device: Device
    tstamp: datetime.datetime
    temp: float
    hum: float
