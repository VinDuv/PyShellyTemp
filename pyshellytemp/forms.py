"""
Application form classes
"""

import dataclasses
import re
import typing

from .models import Device, ShellyV1HTInfo
from .util import float_or_default
from .session import  SessionData, User
from .web import HTTPRequest


@dataclasses.dataclass(frozen=True)
class DeviceFormData:
    """
    Form data for settings common to all devices.
    """

    RGB_HEX_RE = re.compile(r'^#[0-9A-F]{6}$')

    dev_name: str
    temp_color: str
    hum_color: str

    @classmethod
    def from_device(cls, device: Device) -> typing.Self:
        """
        Load the settings from a device.
        """

        return cls(device.name, device.temp_color, device.hum_color)

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> typing.Self:
        """
        Load the settings from a form data dictionary. No validation is done
        at that point.
        """

        dev_name = data.get('dev_name', '').strip()
        temp_color = data.get('temp_color', '').strip().upper()
        hum_color = data.get('hum_color', '').strip().upper()

        return cls(dev_name, temp_color, hum_color)

    def add_to_context(self, ctx: dict[str, typing.Any]) -> None:
        """
        Add the settings to the context so the values can be displayed
        on the settings page.
        """

        ctx['dev_name'] = self.dev_name
        ctx['temp_color'] = self.temp_color
        ctx['hum_color'] = self.hum_color

    def validate(self, errors: list[str]) -> None:
        """
        Validate the current values. If they are invalid, add error messages
        to the provided list.
        """

        if not self.dev_name:
            errors.append("Device name cannot be empty.")

        if not self.RGB_HEX_RE.match(self.temp_color):
            errors.append("Temperature line color is not a valid hex color.")

        if not self.RGB_HEX_RE.match(self.hum_color):
            errors.append("Humidity line color is not a valid hex color.")

    def update_device(self, device: Device) -> None:
        """
        Update the device with the set values. They need to have been
        successfully validated before.
        """

        device.name = self.dev_name
        device.temp_color = self.temp_color
        device.hum_color = self.hum_color


@dataclasses.dataclass
class ShellyV1HTFormData:
    """
    Handle a Shelly v1 H&T configuration settings.
    """

    @dataclasses.dataclass
    class _Credentials:
        dev_uname: str
        dev_pass: str

    @dataclasses.dataclass
    class _Config:
        temp_thresh: str
        hum_thresh: str
        temp_off: str
        hum_off: str

    device: Device
    info: ShellyV1HTInfo
    common: DeviceFormData
    creds: _Credentials
    cfg: _Config

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
        info = self.info

        # Update form values
        self.common = DeviceFormData.from_dict(data)
        self.creds.dev_uname = data.get('dev_uname', '')
        self.creds.dev_pass = data.get('dev_pass', '')
        self.cfg.temp_thresh = data.get('temp_thresh', '').strip()
        self.cfg.hum_thresh = data.get('hum_thresh', '').strip()
        self.cfg.temp_off = data.get('temp_off', '').strip()
        self.cfg.hum_off = data.get('hum_off', '').strip()

        # Determine “cleaned” values
        self.common.validate(errors)

        temp_thresh = float_or_default(self.cfg.temp_thresh, default=-1)
        if not 0 <= temp_thresh <= 20:
            errors.append("Temperature threshold should be between 0 and "
                "15 °C.")
        elif temp_thresh != info.temp_thresh:
            need_config_set = True

        hum_thresh = float_or_default(self.cfg.hum_thresh, default=-1)
        if not 0 <= hum_thresh <= 100:
            errors.append("Humidity threshold should be between 0 and "
                "100.")
        elif hum_thresh != info.hum_thresh:
            need_config_set = True

        temp_off = float_or_default(self.cfg.temp_off, default=-200)
        if not -50 <= temp_off <= 50:
            errors.append("Temperature offset should be between -50 and "
                "+50 °C.")
        elif temp_off != info.temp_off:
            need_config_set = True

        hum_off = float_or_default(self.cfg.hum_off, default=-1)
        if not -50 <= hum_off <= 50:
            errors.append("Humidity offset should be between -50 and "
                "50.")
        elif hum_off != info.hum_off:
            need_config_set = True

        if not errors:
            self.common.update_device(device)
            info.username = self.creds.dev_uname
            info.password = self.creds.dev_pass
            info.temp_thresh = temp_thresh
            info.hum_thresh = hum_thresh
            info.temp_off = temp_off
            info.hum_off = hum_off

            if need_config_set:
                info.need_config_set = True

        return errors, need_config_set

    @classmethod
    def from_info(cls, device: Device, info: ShellyV1HTInfo) -> typing.Self:
        """
        Initialize an instance from device info.
        """

        creds = cls._Credentials(info.username, info.password)
        common = DeviceFormData.from_device(device)
        cfg = cls._Config(str(info.temp_thresh), str(info.hum_thresh),
            str(info.temp_off), str(info.hum_off))

        return cls(device, info, common, creds, cfg)


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
