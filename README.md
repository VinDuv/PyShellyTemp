PyShellyTemp
============

This is a Python Web app that records temperature and humidity values from
Shelly H&T devices, without using the Shelly cloud.

This application is designed to be installed on a computer (probably a NAS or
a single-board computer) which is on the same network as the Shelly devices. It
will provide a report endpoint for the devices to report the sensor values, and
a Web GUI that can be used to see the current and previous sensor values, as
well as configure some settings on the devices.

It also contain a mini Web framework (request routing, database ORM, template
manager) which is fully type-annotated. You may be able to use it on your own
projects; see [the WebFramework file](WebFramework.md) for details.

Installation
------------

PyShellyTemp can be used as a standard WSGI application: the WSGI entry point
is in `pyshellytemp.app`. The following instructions show how to install it
with `uwsgi` and `nginx`.

Create a system user to run the Python code:
```
# useradd -d / -M -r pyshellytemp -s /bin/false
```

Create the database folder:
```
# mkdir -m 770 /var/lib/pyshellytemp
# chown root:pyshellytemp /var/lib/pyshellytemp
```

The permissions are set up so that:
- The `pyshellytemp` user can creates files in the folder (having write
permissions on the database file is not sufficient; SQLite may have to create
additional lock files around it)
- The `pyshellytemp` user cannot alter the folder permissions
- Other users cannot read the database directly

Then, copy the `pyshellytemp` folder (containing `__main__.py`, etc) somewhere
on your system. The `pyshellytemp` user should have read access but *not* write
access to that folder. The following example assumes it has been copied to
`/srv` and is owned by `root`.

As the user owning the files, generate the Python bytecode files:
```
# python3 -m compileall /srv/pyshellytemp/
```

Go to the folder containing the `pyshellytemp` folder, and initialize the
database. The command must be run *as the runtime user* so the database will
have the correct owner.
```
# cd /srv/
# sudo -u pyshellytemp python3 -m pyshellytemp init-db
```

Create a `systemd-tmpfiles` configuration file to manage a
runtime folder for the WSGI socket with the right permissions:
```
# /etc/tmpfiles.d/pyshellytemp.conf
d	/run/pyshellytemp	0750 pyshellytemp www-data
```

(run `systemd-tmpfiles --create` to create the folder immediately)

Add a `systemd` service file for the uwsgi process:
```
# /etc/systemd/system/pyshellytemp.service 
[Unit]
Description=PyShellyTemp temperature logger

[Service]
Type=notify
User=pyshellytemp
ExecStart=/usr/bin/uwsgi \
	--socket /run/pyshellytemp/pyshellytemp.sock --chmod-socket=666 \
	--enable-threads --disable-logging --plugin=python3 \
	--pythonpath=/srv --module=pyshellytemp.app

[Install]
WantedBy=multi-user.target
```

Start and enable the service.

Configure `nginx`:

```
location / {
	include uwsgi_params;
	uwsgi_pass unix:/run/pyshellytemp/pyshellytemp.sock;
	uwsgi_param SCRIPT_NAME '';
}

location /static {
	alias /srv/pyshellytemp/static/;
}
```

If you want to put PyShellyTemp to be served in a subdirectory (`/some/path/`
instead of `/`), set `SCRIPT_NAME` accordingly in `nginx`:
```
location /some/path {
	include uwsgi_params;
	uwsgi_pass unix:/run/pyshellytemp/pyshellytemp.sock;
	uwsgi_param SCRIPT_NAME /some/path;
}
location /some/path/static {
	alias /srv/pyshellytemp/static/;
}
```

also adjust the `uwsgi` options so it sets `PATH_INFO` correctly:
```
ExecStart=/usr/bin/uwsgi \
	--socket /run/pyshellytemp/pyshellytemp.sock --chmod-socket=666 \
	--enable-threads --disable-logging --plugin=python3 \
	--pythonpath=/srv --mount=/some/path=pyshellytemp.app:application --manage-script-name
```

Usage
-----

***Note:*** Since the communication between PyShellyTemp and the Shelly H&T
devices is unsecured, the website only communicates with devices that are on the
local network. This is verified by checking wether the device’s IP address is a
private IPv4 address (as determined by
[IPv4Address.is_private](https://docs.python.org/3/library/ipaddress.html#ipaddress.IPv4Address.is_private))

The main page of the website is public and shows the current sensor values and
the graph of the past values (not implemented at the moment). You can login
from the link in the top right corner, which allows access to the settings page.

The main settings page is in three parts:
- Communication: there is a bi-directional communication between the Shelly H&T
  devices and PyShellyTemp.
  - Shelly devices send a temperature and humidity report to the `/report`
    endpoint of PyShellyTemp. To prevent a rogue device from sending reports,
    the device must previously be registered. This is done by enabling device
    discovery. When enabled, during 10 minutes, any Shelly device that sends a
    report will be automatically registered.
  - PyShellyTemp regularly queries devices to fetch their status (battery level,
    uptime, sensor state, …). This is done using an API that can be password
    protected on the Shelly device; if this is the case, all devices must use
    the same credentials, and it must be set in PyShellyTemp settings.
- Devices: shows all registered devices, with their custom name and status. It
  allows editing or deleting a device.
  - The device settings pages shows the detailed status of the device, and
    allows setting a custom device name that will be shown in PyShellyTemp. It
    also allows setting the report threshold (i.e. the variation of temperature
    and pressure that triggers a report) and the sensor calibration (allows
    setting a corrective offset on the reported values so they better match
    reality).
  - The device information is refreshed every 12 hours; it is done when the
    device is woken up (after a report was send or the device button was
    pressed).
  - The Identify Device feature can be used to determine which device is which
    in the PyshellyTemp interface. After enabling it, press the button on a
    registered device and you will be brought to the device’s settings page.
- Users: User management
  Note that there are no user roles: each user is an administrative user. You
  can create/delete users, change their usernames and passwords (including your
  own).

To register Shelly H&T devices to PyShellyTemp, you can either do it manually
(connect the device to your local network and configure it to send reports to
the `<PyShellyTemp URL>/report` URL, while device discovery is active), or you
can use the `shelly_config.py` Python script. You need to run it on a computer
that:
 - is connected (at least initially) to the local network that the Shelly
   devices will use,
 - has WiFi support so you can connect it to a factory-reset Shelly device.

The script will connect to PyShellyTemp to get the connection parameters
(including the communication username/password that it will be set on the
device), then will wait for your computer to connect to the Shelly device’s
WiFi network, and will then apply the settings. All applied settings will be
logged in the script’s output so you can review them.

Happy monitoring!