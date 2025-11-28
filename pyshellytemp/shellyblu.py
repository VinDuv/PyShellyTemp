#!/usr/bin/env -S python3 -u

"""
Shelly BLU H&T handler. This script should be run in the background to listen
to BLE messages.
"""

import argparse
import asyncio
import collections
import dataclasses
import datetime
import logging
import os
import pathlib
import signal
import socket
import sys
import typing

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

try:
    from bleak.backends.bluezdbus.scanner import BlueZDiscoveryFilters
except ImportError:
    BlueZDiscoveryFilters = dict

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

try:
    from pyshellytemp.models import Device, ShellyBLUHTInfo, Report, DevIdentify
    from pyshellytemp.db import database
except ImportError:
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.absolute()))
    from pyshellytemp.models import Device, ShellyBLUHTInfo, Report, DevIdentify
    from pyshellytemp.db import database


LOGGER = logging.getLogger(__name__)

# Minimum amount of time between handled device messages
DEVICE_REFRESH_INTERVAL = datetime.timedelta(minutes=15)


# MARK: Service management
class ServiceManager:
    """
    Manages startup and stop of the process.
    """

    def __init__(self) -> None:
        self._loop = loop = asyncio.get_running_loop()
        self._quit_evt = asyncio.Event()
        self._notify_sock = self._get_notify_socket()

        loop.add_signal_handler(signal.SIGINT, self._signal_received, 'INT')
        loop.add_signal_handler(signal.SIGTERM, self._signal_received, 'TERM')

    async def ready_wait_quit(self) -> None:
        """
        Signal to the service manager that the service is ready to run. This
        task completes when the service manager ask the service to stop.
        """

        if self._notify_sock is not None and not self._quit_evt.is_set():
            self._notify_sock.sendall(b'READY=1')

        await self._quit_evt.wait()

        self._loop.remove_signal_handler(signal.SIGTERM)
        self._loop.remove_signal_handler(signal.SIGINT)

        if self._notify_sock:
            self._notify_sock.close()

    @staticmethod
    def _get_notify_socket() -> socket.socket | None:
        """
        Get the notify socket from the service manager.
        """

        sock_addr = os.environ.pop('NOTIFY_SOCKET', None)
        if sock_addr is None:
            return None

        if sock_addr[0] == '@':
            sock_addr = '\x00' + sock_addr[1:]
        elif sock_addr[0] != '/':
            return None

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            sock.connect(sock_addr)
        except OSError:
            sock.close()

        return sock

    def _signal_received(self, sig_name: str) -> None:
        LOGGER.info("SIG%s received, quitting", sig_name)
        self._quit_evt.set()

    def __repr__(self) -> str:
        return f"<ServiceManager {self._notify_sock!r}>"


# MARK: Device reception/protocol implementation
class DevLog:
    """
    Log warning/error messages from devices with rate limiting.
    """

    _known_devs: collections.deque[str] = collections.deque(maxlen=50)

    @classmethod
    def log(cls, level: int, dev_id: str, msg: str, *args: typing.Any) -> None:
        """
        Log a message about the specified device ID. If a message was previously
        logged about this device, it will not be logged again.
        """

        is_known = True
        try:
            cls._known_devs.remove(dev_id)
        except ValueError:
            is_known = False

        cls._known_devs.append(dev_id)

        if is_known:
            return

        full_msg = msg % args
        LOGGER.log(level, "Device %s: %s", dev_id, full_msg)

    @classmethod
    def warning(cls, dev_id: str, msg: str, *args: typing.Any) -> None:
        "Log a warning about the specified device ID."
        cls.log(logging.WARNING, dev_id, msg, *args)

    @classmethod
    def error(cls, dev_id: str, msg: str, *args: typing.Any) -> None:
        "Log an error about the specified device ID."
        cls.log(logging.WARNING, dev_id, msg, *args)


T = typing.TypeVar('T')
YieldsQueueOf = typing.AsyncIterator[asyncio.Queue[T]]


class BTHomeParser:
    """
    Parser for BTHome messages.
    """

    class BTHomeInfo(typing.TypedDict, total=False):
        """
        BTHome sensor information. The fields depend on the device.
        """

        packet_id: int # Packet ID, 0-255
        bat_level: int # Battery level, 0-100
        humidity: int # Relative humidity, 0-100
        temperature: float # Temperature, °C
        button: typing.Required[bool] # Button pressed
        dev_type: int # Device type ID
        fw_ver: str # Firmware version

    def __init__(self) -> None:
        self._data = b''
        self._cur_pos = 0
        self._end_pos = 0

    def parse(self, data: bytes) -> BTHomeInfo:
        """
        Parse the provided data message into a BTHomeInfo dictionary, from the
        specified start offset. Raises a MsgParseError if the message cannot be
        parsed.
        """

        self._data = data
        self._cur_pos = 0
        self._end_pos = len(data)

        info = self.BTHomeInfo(button=False)

        while self._cur_pos < self._end_pos:
            obj_id = self._read_int(1)

            if obj_id == 0x00:
                info['packet_id'] = self._read_int(1)
            elif obj_id == 0x01:
                info['bat_level'] = self._read_int(1)
            elif obj_id == 0x2e:
                info['humidity'] = self._read_int(1)
            elif obj_id == 0x3a:
                info['button'] = self._read_int(1) != 0
            elif obj_id == 0x45:
                info['temperature'] = self._read_int(2, signed=True) * 0.1
            elif obj_id == 0xf0:
                info['dev_type'] = self._read_int(2)
            elif obj_id == 0xf1:
                info['fw_ver'] = self._read_version_bytes(4)
            elif obj_id == 0xf2:
                info['fw_ver'] = self._read_version_bytes(3)
            else:
                raise MsgParseError(f"Error parsing {self._data!r}: "
                    f"Unknown object ID {obj_id:x} at offset "
                    f"{self._end_pos - 1}")

        self._data = b''

        return info

    def _read_int(self, size: int, *, signed: bool = False) -> int:
        """
        Read the specified number of bytes as a little-endian integer,
        and return it.
        """

        cur_pos = self._cur_pos
        after_pos = self._cur_pos + size
        if after_pos > self._end_pos:
            raise MsgParseError(f"Error parsing {self._data!r}: EOF reading "
                f"{size} bytes at position {self._cur_pos}")

        self._cur_pos = after_pos
        return int.from_bytes(self._data[cur_pos:after_pos], byteorder='little',
            signed=signed)

    def _read_version_bytes(self, size: int) -> str:
        """
        Read a little-endian version number of the specified length.
        """

        parts: collections.deque[str] = collections.deque()
        for _ in range(size):
            parts.appendleft(str(self._read_int(1)))

        return ".".join(parts)

    def __repr__(self) -> str:
        return "<BTHomeParser>"


@dataclasses.dataclass(frozen=True)
class ShellyBLUMessage:
    """
    Message received from a Shelly BLU device.
    """

    # Device that received the message
    dev: BLEDevice

    # Device MAC address as a binary string
    dev_mac: bytes

    # Receive date/time
    recv_dt: datetime.datetime

    # Signal strength
    rssi: int

    # Raw message data; may be encrypted
    raw_data: bytes

    @property
    def is_encrypted(self) -> bool:
        "Indicates if the message is encrypted."
        return (self.raw_data[0] & 0x01) == 1

    @property
    def dev_id(self) -> str:
        "Device ID at the Bluetooth stack level"
        return str(self.dev.address)

    def decode_with(self, parser: BTHomeParser, *, enc_key: bytes = b'') -> \
        BTHomeParser.BTHomeInfo:
        """
        Decrypts (if necessary) and decode the message to BTHome info using
        the specified parser.
        Raises KeyRequired if the message is encrypted and the encryption key
        is either missing or has the wrong size.
        Raises MsgDecryptError if the message is encrypted but fails
        verification once decrypted.
        """

        return parser.parse(self.get_bthome_data(enc_key=enc_key))

    def get_bthome_data(self, *, enc_key: bytes = b'') -> bytes:
        """
        Get the raw BTHome data from the message. If the message is encrypted,
        it will decrypted using the provided key.
        Raises KeyRequired if the message is encrypted and the encryption key
        is either missing or has the wrong size.
        Raises MsgDecryptError if the message is encrypted but fails
        verification once decrypted.
        """

        raw_data = self.raw_data

        if not self.is_encrypted:
            return self.raw_data[1:]

        if len(enc_key) != 16:
            raise KeyRequired(f"Specified key is {len(enc_key)} (!= 16)")

        ciphertext = raw_data[1:-8]
        counter = raw_data[-8:-4]
        mic = raw_data[-4:]

        nonce = self.dev_mac + b'\xd2\xfc' + raw_data[0:1] + counter

        aesccm = AESCCM(enc_key, tag_length=4)
        try:
            return aesccm.decrypt(nonce, ciphertext + mic, None)
        except InvalidTag:
            raise MsgDecryptError("Corrupted message or invalid key") from None


class ShellyBLUMessageReceiver:
    """
    Receiver for Shelly BLU H&T messages.
    """

    def __init__(self, *, filter_duplicates: bool = True) -> None:
        # Filter messages by Shelly BLU MAC to reduce CPU load (BlueZ stack
        # only for now)
        filters = BlueZDiscoveryFilters(Pattern='7C:C6:B6')

        self._scanner = BleakScanner(self._recv_cb, bluez={'filters': filters})
        self._queue: asyncio.Queue[ShellyBLUMessage | None] | None = None
        self._exception: Exception | None = None
        self._prev_handler: typing.Callable[[asyncio.AbstractEventLoop,
            dict[str, typing.Any]], object] | None = None
        self._filter_duplicates = filter_duplicates
        self._dup_filter: collections.deque[tuple[str, bytes]] = \
            collections.deque(maxlen=10)

    def __aiter__(self) -> typing.Self:
        assert self._queue is not None, "Not started"
        return self

    async def __anext__(self) -> ShellyBLUMessage:
        assert self._queue is not None, "Not started"

        item = await self._queue.get()
        if item is None:
            self._raise_stowed_exception()
            assert False, "None in queue but not stowed?"

        return item

    async def __aenter__(self) -> typing.Self:
        """
        Starts the scanner and registers an exception handler so that
        callback exceptions are reported.
        """

        assert self._queue is None, "Already started?"

        loop = asyncio.get_running_loop()
        self._prev_handler = loop.get_exception_handler()
        loop.set_exception_handler(self._exc_cb)

        self._queue = asyncio.Queue()

        await self._scanner.start()

        return self

    async def __aexit__(self, *_args: typing.Any) -> None:
        """
        Stops the scanner and restores the original exception state.
        """

        assert self._queue is not None, "Not started?"

        await self._scanner.stop()
        self._queue = None

        self._raise_stowed_exception()

    def _recv_cb(self, dev: BLEDevice, ad: AdvertisementData) -> None:
        """
        Handle a Bluetooth message. If it is from a Shelly BLU device, decodes
        it and puts it in the queue.
        """

        assert self._queue is not None, "Not started?"

        mf_data = ad.manufacturer_data.get(0x0BA9)
        svc_data = ad.service_data.get('0000fcd2-0000-1000-8000-00805f9b34fb')
        if mf_data is None or not svc_data:
            return

        cur_dt = datetime.datetime.now()

        pos = 0
        end = len(mf_data)
        dev_model = -1
        dev_mac = b''

        while pos < end:
            block_type = mf_data[pos]
            pos += 1
            if block_type == 0x01:
                # Feature/status flags (unused)
                pos += 2
            elif block_type == 0x0A:
                # MAC address (little endian!)
                dev_mac = mf_data[pos + 5:pos - 1:-1]
                pos += 6
            elif block_type == 0x0B:
                # Device model identifier
                dev_model = int.from_bytes(mf_data[pos:pos + 2],
                    byteorder='little')
                pos += 2
            else:
                # Invalid block type, probably not a Shelly device
                dev_model = -1
                DevLog.warning(dev.address, "Failed to parse MF data %r",
                    mf_data)
                break

        # Check for Shelly BLU H&T identifier and valid MAC
        if dev_model != 0x0003 or len(dev_mac) != 6:
            return

        # Filter message duplicates
        key = (dev.address, svc_data)
        duplicate = True
        try:
            self._dup_filter.remove(key)
        except ValueError:
            duplicate = False

        self._dup_filter.append(key)
        if self._filter_duplicates and duplicate:
            return

        msg = ShellyBLUMessage(dev, dev_mac, cur_dt, ad.rssi, svc_data)
        self._queue.put_nowait(msg)

    def _exc_cb(self, _loop: asyncio.AbstractEventLoop,
        context: dict[str, typing.Any]) -> None:
        """
        Stores unhandled exception and wakes up the queue so they can be raised
        later.
        """

        assert self._queue is not None, "Exception with no queue?"

        self._exception = context.get('exception',
            Exception(context['message']))

        self._queue.put_nowait(None)

    def _raise_stowed_exception(self) -> None:
        """
        Raises any stowed callback exception.
        """

        exc = self._exception

        if exc is not None:
            self._exception = None
            raise exc


# MARK: Exception classes
class MsgParseError(Exception):
    """
    Raised when a message is not a a valid BTHome message.
    """


class KeyRequired(Exception):
    """
    Raised when decoding a messages require a key, and the provided one is
    either missing or invalid (wrong size).
    """


class MsgDecryptError(Exception):
    """
    Raised when the message fails to decrypt properly, indicating that the
    message was corrupted or the key is invalid.
    """


# MARK: Main processing
def update_dev_from_msg(parser: BTHomeParser, dev: Device,
    info: ShellyBLUHTInfo, msg: ShellyBLUMessage) -> bool:
    """
    Update the device status from the message and generate a report if the
    message is correct.
    Returns True if the device data should be saved.
    """

    dev_id = msg.dev_id

    status = Device.Status.OK

    refresh_dt = dev.last_report + DEVICE_REFRESH_INTERVAL

    # Assume no need to save if we have recent data
    should_save = msg.recv_dt >= refresh_dt

    enc_key = bytes.fromhex(info.enc_key)
    try:
        bthome_info = msg.decode_with(parser, enc_key=enc_key)
    except KeyRequired:
        DevLog.error(dev_id, "Key required for message decryption")
        status = Device.Status.AUTH_ERROR
        should_save = True
    except MsgDecryptError:
        DevLog.error(dev_id, "Message decryption failed (wrong key?)")
        status = Device.Status.AUTH_ERROR
        should_save = True
    except MsgParseError as err:
        DevLog.error(dev_id, "Message parse error: %s", err)
        status = Device.Status.BAD_DATA
        should_save = True

    if status is Device.Status.OK:
        try:
            dev.bat_percent = bthome_info['bat_level']
        except KeyError as err:
            DevLog.error(dev_id, "Message parse error: missing key %s", err)
            status = Device.Status.BAD_DATA
            should_save = True

        humidity = bthome_info.get('humidity')
        temperature = bthome_info.get('temperature')
        fw_ver = bthome_info.get('fw_ver')
        if fw_ver is not None:
            should_save = True
            info.fw_ver = fw_ver

        if status is Device.Status.OK:
            if dev.status is not Device.Status.OK:
                should_save = True

            if should_save and temperature is not None and humidity is not None:
                dev.last_temp = temperature
                dev.last_hum = humidity

                Report(dev, msg.recv_dt, temperature, humidity)

            if bthome_info['button']:
                DevIdentify.device_identified(dev_id)

    if dev.status is not status:
        dev.status = status
        should_save = True

    return should_save


def process_dev_msg(parser: BTHomeParser, msg: ShellyBLUMessage) -> None:
    """
    Process a message from a device.
    """

    dev_id = msg.dev_id


    try:
        dev = Device.get_one(ident=dev_id)
    except KeyError:
        dev = None

    if dev is None:
        try:
            dev = Device.get_one(ident=ShellyBLUHTInfo.REG_IDENT)
        except KeyError:
            DevLog.warning(dev_id, "Device is not registered.")
            return

        dev.ident = dev_id
        dev.name = dev_id
        # Force refresh
        dev.last_report = datetime.datetime(2000, 1, 1)

        LOGGER.info("Finishing new registration of device %s", dev_id)

    elif dev.type is not Device.Type.SHELLY_BLU_H_T:
        DevLog.warning(dev_id, "ID conflict with another device type.")
        return

    info = ShellyBLUHTInfo.get_one(device=dev)

    if not update_dev_from_msg(parser, dev, info, msg):
        # Valid message too close to last message, no need to update info
        LOGGER.debug("Message from %s ignored, last message too recent", dev_id)
        return

    dev.last_report = msg.recv_dt
    info.mac_addr = ':'.join(f'{item:02x}' for item in msg.dev_mac)
    info.rssi = int(msg.rssi)

    LOGGER.debug("Saving information from %s", dev_id)

    dev.save()
    info.save()


async def process() -> None:
    """
    Main process task
    """

    parser = BTHomeParser()
    async with ShellyBLUMessageReceiver() as receiver:
        async for msg in receiver:
            LOGGER.debug("Processing message from device %s", msg.dev_id)
            process_dev_msg(parser, msg)


async def main() -> None:
    """
    Asynchronous entry point
    """

    parser = argparse.ArgumentParser(description="Shelly BLU background "
        "handler")
    parser.add_argument('-d', '--debug', action='store_const', dest='log_level',
        const=logging.DEBUG, default=logging.INFO, help="Debug logging")
    parser.add_argument('--db-path', help="Database path")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(message)s')
    LOGGER.setLevel(args.log_level)

    manager = ServiceManager()

    if args.db_path is not None:
        database.set_db_path(args.db_path)
    blu_count = Device.get_all(type=Device.Type.SHELLY_BLU_H_T).count()

    LOGGER.info("Shelly BLU device processing initializing, %d device(s) "
        "currently registered.", blu_count)

    # The task group is there to catch any unhandled exceptions
    async with asyncio.TaskGroup() as tg:
        # Start the main task
        process_task = tg.create_task(process())

        # Wait for stop event
        await tg.create_task(manager.ready_wait_quit())

        # Stop the main task
        process_task.cancel()


if __name__ == '__main__':
    asyncio.run(main())
