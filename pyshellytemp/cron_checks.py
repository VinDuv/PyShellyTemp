#!/usr/bin/env -S python3 -u

"""
Script to be executed via cron. Check the devices status. By default,
prints nothing if everything is OK; returns a failure code if any device has
an error.
"""

import argparse
import datetime
import logging
import pathlib
import sys

try:
    from pyshellytemp.models import Device
    from pyshellytemp.db import database
except ImportError:
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.absolute()))
    from pyshellytemp.models import Device
    from pyshellytemp.db import database


INTERVAL_WITHOUT_REPORT = datetime.timedelta(hours=12)
BATT_PERCENT_WARNING = 20
LOGGER = logging.getLogger(__name__)


def run_checks() -> bool:
    """
    Runs the device checks.  Returns True iff all the checks succeed.
    """

    ok = True

    warn_dt = datetime.datetime.now() - INTERVAL_WITHOUT_REPORT

    for device in Device.get_all():
        LOGGER.debug("Checking device %r", device.name)
        ok = check_device(device, warn_dt) and ok

    return ok


def check_device(device: Device, warn_dt: datetime.datetime) -> bool:
    """
    Checks a device. Returns True iff all the checks succeed.
    """

    if device.status is not Device.Status.OK:
        LOGGER.warning("Warning: Device %s status is '%s'", device.name,
            device.status)
        return False

    if device.last_report < warn_dt:
        date_str = device.last_report.strftime('%Y-%m-%d %H:%M:%S')
        LOGGER.warning("Device %s last report was at %s", device.name, date_str)
        return False

    if device.bat_percent < BATT_PERCENT_WARNING:
        LOGGER.warning("Device %s battery is at %d%%", device.name,
            device.bat_percent)
        return False

    return True


def run() -> None:
    """
    Checker entry point
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db-path', help="Database path")
    parser.add_argument('-d', '--debug', action='store_const', dest='log_level',
        const=logging.DEBUG, default=logging.INFO, help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(format='%(message)s', level=logging.INFO)
    LOGGER.setLevel(args.log_level)

    if args.db_path is not None:
        database.set_db_path(args.db_path)

    if not run_checks():
        sys.exit(1)


if __name__ == '__main__':
    run()
