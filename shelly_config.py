#!/usr/bin/env python3

"""
This script auto-configures a Shelly H&T device to communicate with
PyShellyTemp.
"""

from http.client import HTTPConnection
from urllib.parse import urlparse, urlunparse, urlencode
import argparse
import base64
import errno
import getpass
import ipaddress
import json
import time
import typing
import socket
import sys


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
    settings = srv_conn.get_settings()
    while True:
        dev_conn = DeviceConn.wait_for_device()
        srv_conn.enable_discovery()

        dev_conn.apply_settings(settings)

        res = input('Configure another device? [y/N] ').lower()
        if res not in {'y', 'yes'}:
            return


class DevSettings(typing.NamedTuple):
    """
    Configuration parameters for the Shelly device
    """

    report_url: str
    dev_username: str
    dev_password: str
    wifi_ssid: str
    wifi_password: str


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

    def __init__(self, conn: HTTPConnection):
        self._conn = conn
        self._auth_header = ''

    def apply_settings(self, settings: DevSettings) -> None:
        """
        Apply the settings to the specified device.
        """

        print("Configuring device...")
        print(" - Setting up report action")
        urls_arg = {'urls[]': settings.report_url}
        result = self._post('/settings/actions', index='0',
            name='report_url', enabled='true', **urls_arg)
        print(f"   {result['actions']['report_url']}")

        print(" - Disabling cloud")
        result = self._post('/settings/cloud', enabled='false')
        print(f"    {result}")

        print(" - Disabling CoIoT and MQTT")
        result = self._post('/settings', coiot_enable='false',
            mqtt_enable='false')
        print(f"    {result}")

        if settings.dev_username:
            print(" - Setting up authentification")
            result = self._post('/settings/login', enabled='true',
                username=settings.dev_username, password=settings.dev_password)
            result['password'] = '*******'
            print(f"   {result}")

            # Need auth for the rest of the config
            auth_str = f'{settings.dev_username}:{settings.dev_password}'
            auth_64 = base64.b64encode(auth_str.encode('utf-8')).decode('ascii')
            self._auth_header = f'Basic {auth_64}'

        print(" - Configuring WiFi")
        result = self._post('/settings/sta', enabled='true',
            ssid=settings.wifi_ssid, key=settings.wifi_password)
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

                return cls(conn)

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
            srv_url = input('URL: ')
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

            if not ipaddress.IPv4Address(ipaddress).is_private:
                print(f"The IP address {ip_addr} from hostname {hostname} is "
                    f"not a local IP address. You need to specify the "
                    f"network-local address of the server so when the Shelly "
                    f"devices connect to it, the server can determine their "
                    f"adresses.")
                continue

            conn = HTTPConnection(ip_addr, port, timeout=1)
            try:
                cls._send_req(conn, base_path + '/autoconf', username='',
                password='')
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

    def get_settings(self) -> DevSettings:
        """
        Queries the user and the PyShellyTemp server to determine the settings
        to apply to the Shelly device, and returns them.
        """

        while True:
            self._username = input("Username: ")
            self._password = getpass.getpass("Password: ")

            try:
                json_data = self._send_req(self._conn,
                    self._base_path + '/autoconf', username=self._username,
                    password=self._password)
            except self.BadStatus:
                print("Authentification failed")
                continue

            dev_username: str = json_data['dev_username']
            dev_password: str = json_data['dev_password']
            break

        report_url = urlunparse(('http', self._server,
            self._base_path + '/report', '', '', ''))

        print(f"Report URL: {report_url}")
        print("")
        print("Enter the WiFi settings that will be used by the Shelly H&T:")
        wifi_ssid = ''
        while not wifi_ssid:
            wifi_ssid = input('SSID: ')
        wifi_password = getpass.getpass('Password: ')

        if bool(dev_username) != bool(dev_password):
            sys.exit("Device username and password must both be set or unset.")

        return DevSettings(report_url, dev_username, dev_password,
            wifi_ssid, wifi_password)

    def enable_discovery(self) -> None:
        """
        Enable discovery on the server.
        """

        self._send_req(self._conn, self._base_path + '/autoconf',
            username=self._username, password=self._password, discovery='1')


    @classmethod
    def _send_req(cls, conn: HTTPConnection, path: str,
        **kwargs: str) -> typing.Any:
        """
        Sends a POST request on the connection and reads the response.
        Raises BadStatus if the response status is not 200.
        Returns the response decoded as JSON.
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


if __name__ == '__main__':
    run()
