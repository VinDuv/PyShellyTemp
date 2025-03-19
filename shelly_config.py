#!/usr/bin/env python3

"""
This script auto-configures a Shelly H&T device to communicate with
PyShellyTemp.
"""

from http.client import HTTPConnection, RemoteDisconnected
from urllib.parse import urlparse, urlunparse, urlencode
import argparse
import base64
import errno
import getpass
import ipaddress
import json
import secrets
import socket
import string
import sys
import time
import typing


PWD_CHARS = string.ascii_letters + string.digits


def run() -> None:
    """
    Program entry point
    """

    parser = argparse.ArgumentParser(description="Shelly H&T autoconfiguration")
    parser.parse_args()

    print("This scripts automatically configures a Shelly H&T device to send "
        "reports to PyShellyTemp. The PyShellyTemp server must be configured "
        "beforehand. You will need an account on it.")
    print("Enter the URL of the PyShellyTemp server (HTTP):")
    srv_conn = ServerConn.query_user()
    srv_conn.authenticate()

    wifi = WiFiSettings.query()

    while True:
        res = prompt_yn("Set up authentication on the device?")
        if res:
            print("Enter the username and password you wish to use. Leave "
                "blank to use auto-generated values.")
            dev_uname = input_or_exit("Username: ") or 'shellyuser'
            dev_passwd = input_or_exit("Password: ", pwd=True)
            if not dev_passwd:
                dev_passwd = ''.join(secrets.choice(PWD_CHARS) for _ in
                    range(10))
        else:
            dev_uname = ''
            dev_passwd = ''

        device = DeviceConn.wait_for_device()
        dev_ident = device.dev_ident

        print(f"Registering device {dev_ident}…")
        report_url = srv_conn.register(dev_ident, dev_uname, dev_passwd)

        print("Configuring device...")
        device.apply_settings(report_url, wifi, dev_uname, dev_passwd)

        if not prompt_yn("Configure another device?", default=False):
            return


class WiFiSettings(typing.NamedTuple):
    """
    WiFi configuration settings.
    """

    ssid: str
    password: str

    @classmethod
    def query(cls) -> typing.Self:
        """
        Prompt the user for the WiFi configuration.
        """

        print("Enter the WiFi settings that will be used by the Shelly H&T:")

        wifi_ssid = ''
        while not wifi_ssid:
            wifi_ssid = input_or_exit('SSID: ')

        ask_disp = True

        while True:
            wifi_password = input_or_exit('Password: ', pwd=True)

            if ask_disp:
                res = prompt_yn("Display entered password for 2 secs?",
                    default=False)
                if not res:
                    return cls(wifi_ssid, wifi_password)

                ask_disp = False

            print(wifi_password, end='', flush=True)
            time.sleep(3)
            print('\r' + ' ' * len(wifi_password) + '\r', end='',
                flush=True)

            if prompt_yn("OK?"):
                return cls(wifi_ssid, wifi_password)


class DeviceConn:
    """
    Connection to a Shelly H&T device currently being configured over WiFi.
    """

    class NoRouteHTTPConn(HTTPConnection):
        """
        HTTPConnection subclass that sets SO_DONTROUTE on the created socket.
        """

        def __init__(self, host: str, port: int, *, timeout: int):
            super().__init__(host, port, timeout=timeout)

            self._create_connection = self._create_connection_no_route

        @staticmethod
        def _create_connection_no_route(host_port: tuple[str, int],
            timeout: int, src_addr: tuple[str, int] | None) -> socket.socket:
            """
            Creates a connection like socket.create_connection, but with
            SO_DONTROUTE enabled.
            """

            conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            conn.settimeout(timeout)
            if src_addr:
                conn.bind(src_addr)
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_DONTROUTE, True)
            conn.connect(host_port)

            return conn

    def __init__(self, conn: HTTPConnection, dev_ident: str):
        self._conn = conn
        self._dev_ident = dev_ident
        self._auth_header = ''

    @property
    def dev_ident(self) -> str:
        """
        Return the device ID.
        """

        return self._dev_ident

    def apply_settings(self, report_url: str, wifi: WiFiSettings,
        dev_uname: str, dev_passwd: str) -> None:
        """
        Apply the settings to the specified device.
        """

        print("Configuring device...")
        print(" - Setting up report action")
        urls_arg = {'urls[]': report_url}
        result = self._post('/settings/actions', index='0',
            name='report_url', enabled='true', **urls_arg)
        print(f"   {result['actions']['report_url']}")

        print(" - Disabling cloud")
        result = self._post('/settings/cloud', enabled='false')
        print(f"    {result}")

        print(" - Disabling CoIoT, MQTT, MDNS, and time management")
        result = self._post('/settings', coiot_enable='false',
            mqtt_enable='false', discoverable='false', sntp_server='',
            timezone='UTC', lat='0', lng='0', tzautodetect='false',
            tz_utc_offset='0', tz_dst='0', tz_dst_auto='0')
        print(f"    {result}")

        if dev_uname or dev_passwd:
            print(" - Setting up authentification")
            result = self._post('/settings/login', enabled='true',
                username=dev_uname, password=dev_passwd)
            result['password'] = '*******'
            print(f"   {result}")

            # Need auth for the rest of the config
            auth_str = f'{dev_uname}:{dev_passwd}'
            auth_64 = base64.b64encode(auth_str.encode('utf-8')).decode('ascii')
            self._auth_header = f'Basic {auth_64}'

        print(" - Configuring WiFi")
        result = self._post('/settings/sta', enabled='true', ssid=wifi.ssid,
            key=wifi.password)
        print(f"   {result}")

        print("Configuration finished.")

    @classmethod
    def wait_for_device(cls) -> typing.Self:
        """
        Waits for a Shelly device’s AP network to be joined by the computer.
        Returns a device connection.
        """

        print("Power up the Shelly H&T device you want to configure, press its "
            "button for 10 seconds (LED flashes slowly then more rapidly), "
            "then connect to the 'shellyht-XXXXXX' WiFi network.")

        print("Waiting for connection...")
        while True:
            conn = cls.NoRouteHTTPConn('192.168.33.1', 80, timeout=5)
            try:
                conn.request('GET', '/shelly')
                with conn.getresponse() as response:
                    if response.status == 200:
                        data = json.load(response)
                    else:
                        print(f"Unexpected response status {response.status} "
                            "from device. Retrying…")
                        time.sleep(2)
                        continue

                dev_type = data.get('type', '<unknown>')
                if dev_type != 'SHHT-1':
                    sys.exit(f"Wrong device type: {dev_type}")

                if data['auth']:
                    print("Connected to a device with authentication enabled. "
                        "Cannot continue.")
                    print("Make sure you reset the device completely.")
                    time.sleep(10)
                    continue

                print(f"Connected to Shelly H&T 1, MAC {data['mac']}, firmware "
                    f"{data['fw']}")

                dev_ident: str = data['mac'][6:]

                return cls(conn, dev_ident)

            except TimeoutError:
                # Should not happen since routing is disabled, but let’s allow
                # it anyway
                time.sleep(1)
            except OSError as err:
                if err.errno == errno.ENETUNREACH:
                    time.sleep(1)
                else:
                    raise

    def _post(self, path: str, **kwargs: str) -> typing.Any:
        """
        Sends a POST request to the device, with the specified parameters.
        """

        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
        }

        if self._auth_header:
            headers['Authorization'] = self._auth_header

        body = urlencode(kwargs)
        print(body)
        self._conn.request('POST', path, headers=headers, body=body)

        with self._conn.getresponse() as response:
            if response.status == 200:
                return json.load(response)

            data = response.read(100).decode('utf-8', 'replace')
            sys.exit(f"{path}: bad status code {response.status}: {data}")


class ServerConn:
    """
    Connection to the PyShellyTemp server to get configuration information
    and enable discovery.
    """

    def __init__(self, conn: HTTPConnection, server: str, base_path: str):
        self._conn = conn
        self._server = server
        self._base_path = base_path
        self._username = ''
        self._password = ''

    @classmethod
    def query_user(cls) -> typing.Self:
        """
        Asks the user for the server URL and returns a connection object.
        """

        while True:
            srv_url = input_or_exit('URL: ')

            if not srv_url:
                sys.exit(0)

            parts = urlparse(srv_url)
            if parts.scheme not in {'', 'http'}:
                print("Scheme must be http://")
                continue

            if parts.hostname is None:
                print("No hostname specified")
                continue

            base_path = parts.path.rstrip('/')

            hostname = parts.hostname
            port = parts.port or 80

            try:
                ip_addr = socket.getaddrinfo(parts.hostname, port,
                    socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
            except OSError as err:
                print(f"Unable to resolve hostname: {err}")
                continue

            assert isinstance(ip_addr, str)

            if not ipaddress.IPv4Address(ip_addr).is_private:
                print(f"The IP address {ip_addr} from hostname {hostname} is "
                    f"not a local IP address. You need to specify the "
                    f"network-local address of the server so when the Shelly "
                    f"devices connect to it, the server can determine their "
                    f"adresses.")
                continue

            conn = HTTPConnection(ip_addr, port, timeout=10)
            try:
                cls._send_req(conn, base_path + '/autoconf', mode='auth',
                    username='', password='')
            except ConnectionError as err:
                print(f"Connection error: {err}")
                continue
            except cls.BadStatus as err:
                status = err.status
            else:
                status = 200

            if status != 403:
                print(f"Unexpected status {status}. Check that the URL is "
                    f"correct.")
                conn.close()
                continue

            server = hostname if port == 80 else f"{hostname}:{port}"

            return cls(conn, server, base_path)

    def authenticate(self) -> None:
        """
        Authenticate on the server.
        """

        while True:
            self._username = input_or_exit("Username: ")
            self._password = input_or_exit("Password: ", pwd=True)

            try:
                self._send_req(self._conn, self._base_path + '/autoconf',
                    mode='auth', username=self._username,
                    password=self._password)
            except self.BadStatus:
                print("Authentification failed")
                continue

            return

    def register(self, dev_ident: str, dev_uname: str, dev_passwd: str) -> str:
        """
        Register a new device on the server. If a device with that ID already
        exists, its username and password are updated.
        Returns the URL that the device needs to send its reports to.
        """

        self._send_req(self._conn, self._base_path + '/autoconf', mode='reg',
            dev_ident=dev_ident, dev_uname=dev_uname, dev_passwd=dev_passwd,
            username=self._username, password=self._password)

        return urlunparse(('http', self._server, self._base_path + '/report',
            '', '', ''))

    @classmethod
    def _send_req(cls, conn: HTTPConnection, path: str,
        **kwargs: str) -> typing.Any:
        """
        Sends a POST request on the connection and reads the response.
        Raises BadStatus if the response status is not 200.
        Returns the response decoded as JSON.
        """

        try:
            return cls._send_req_noretry(conn, path, **kwargs)
        except (RemoteDisconnected, BrokenPipeError, ConnectionResetError):
            # Close the connection so it is re-established
            conn.close()
            return cls._send_req_noretry(conn, path, **kwargs)

    @classmethod
    def _send_req_noretry(cls, conn: HTTPConnection, path: str,
        **kwargs: str) -> typing.Any:
        """
        Sends a POST request on the connection and reads the response.
        Raises BadStatus if the response status is not 200.
        Returns the response decoded as JSON.
        Does not retry if a BrokenPipeError error is encountered
        """

        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
        }
        conn.request('POST', path, headers=headers, body=urlencode(kwargs))

        with conn.getresponse() as response:
            if response.status == 200:
                return json.load(response)

            raise cls.BadStatus(response.status, response.read(100))

    class BadStatus(Exception):
        """
        Exception raised when a request returns an invalid HTTP status
        """

        def __init__(self, status: int, data: bytes):
            self.status = status
            super().__init__(f"Bad status {status}: {data!r}")


def prompt_yn(prompt: str, default: bool = True) -> bool:
    """
    Prompts the user for a yes/no question.
    """

    if default:
        prompt = f"{prompt} [Y/n] "
    else:
        prompt = f"{prompt} [y/N] "

    while True:
        res = input_or_exit(prompt).lower()

        if res == "":
            return default

        if res in {"y", "yes"}:
            return True

        if res in {"n", "no"}:
            return False


def input_or_exit(prompt: str, pwd: bool = False) -> str:
    """
    Prompt the user; exits the program if EOF or Ctrl-C is encountered.
    """

    try:
        if pwd:
            return getpass.getpass(prompt)
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        sys.exit("")


if __name__ == '__main__':
    run()
