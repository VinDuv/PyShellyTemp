"""
Module entry point; contain admin tools
"""

import argparse
import getpass
import sys
import time

from . import models
from .db import database
from .db.upgrade import DatabaseUpgrader
from .models import Device, Report
from .session import User


def run() -> None:
    """
    Admin tools entry point
    """

    parser = argparse.ArgumentParser(prog='python3 -m pyshellytemp',
        description="PyShellyTemp admin tools")

    parser.add_argument('--db-path', help="Database path")

    sub = parser.add_subparsers(title="Available commands", metavar='command',
        required=True)

    init_db_p = sub.add_parser('init-db', aliases=['init'],
        help="Initialize the database")
    init_db_p.add_argument('-f', '--force', action='store_true',
        help="Re-creates the database even if it already exists")
    init_db_p.add_argument('--no-create-user', action='store_false',
        dest='create_user',
        help="Disable the prompt to create a user in the database")
    init_db_p.set_defaults(func=_init_db)

    create_user = sub.add_parser('create-user',
        help="Create an administrative user")
    create_user.add_argument('username', nargs='?', default='',
        help="Name of the user to create (leave blank for prompt)")
    create_user.add_argument('password', nargs='?', default='',
        help="Password of the user to create (leave blank for prompt)")
    create_user.set_defaults(func=_create_user)

    upgrade_db = sub.add_parser('upgrade-db', help="Upgrade the database")
    upgrade_db.set_defaults(func=_upgrade_db)

    copy_data = sub.add_parser('copy-data', help="Copy history data from "
        "device to device. Only the previous device data prior to the new "
        "device data will be copied.")
    copy_data.add_argument('from_name', help="Device to copy data from",
        metavar='from')
    copy_data.add_argument('to_name', help="Device to copy data to",
        metavar='to')
    copy_data.set_defaults(func=_copy_data)

    args = parser.parse_args()

    if args.db_path is not None:
        database.set_db_path(args.db_path)

    args.func(args)


def _init_db(args: argparse.Namespace) -> None:
    """
    Initializes the database.
    """

    database.init(force=args.force)

    if args.create_user:
        print("Database created. Creating initial admin user.")
        _create_user()


def _create_user(args: argparse.Namespace | None = None) -> None:
    """
    Creates an administrative user in the database.
    """

    if args is None:
        username = ''
        password = ''
    else:
        username = args.username
        password = args.password

    while not username:
        username = input('Username: ')

    while not password:
        password = getpass.getpass('Password: ')

    User.create_user(username, password)


def _upgrade_db(_args: argparse.Namespace | None = None) -> None:
    """
    Perform a database upgrade.
    """

    upgrader = DatabaseUpgrader(models)
    upgrader.do_upgrade()


def _copy_data(args: argparse.Namespace) -> None:
    """
    Copy data from a device to another.
    """

    from_name: str = args.from_name
    to_name: str = args.to_name

    from_dev = _get_dev(from_name)
    to_dev = _get_dev(to_name)

    to_start = list(Report.get_all(device=to_dev).order_by('tstamp')[0:1])
    cutoff_date = to_start[0].tstamp if to_start else None

    if cutoff_date is None:
        count = Report.get_all(device=from_dev).count()
        print(f"Will copy all data from {from_name!r} to {to_name!r} "
            f"({count} records)")
        cutoff_tstamp = time.time()
    else:
        count = Report.get_all(device=from_dev, tstamp__lt=cutoff_date).count()
        date_str = cutoff_date.strftime('%Y-%m-%d %H:%M:%S')
        print(f"Will copy all data before {date_str} from {from_name!r} to"
            f" {to_name!r} ({count} records)")
        cutoff_tstamp = cutoff_date.timestamp()

    if not count:
        sys.exit("Nothing to copy!")

    query = '''
        insert into reports (device_id, tstamp, temp, hum)
            select ?, tstamp, temp, hum
                from reports
                where device_id = ?
                    and tstamp < ?
        ;
    '''

    while True:
        res = input("Execute? [y/N] ").lower()
        if res in {'y', 'yes'}:
            break

        if res in {'n', 'no', ''}:
            return

    database.exec_raw(query, (to_dev.id, from_dev.id, cutoff_tstamp))


def _get_dev(name: str) -> Device:
    """
    Gets a device from a name or identifier.
    """

    try:
        return Device.get_one(ident=name)
    except KeyError:
        try:
            return Device.get_one(name=name)
        except KeyError:
            sys.exit(f"Unknown device name or identifier {name!r}.")
        except ValueError:
            sys.exit(f"Multiple devices have the name {name!r}. Specify their "
                "identifier instead.")


if __name__ == '__main__':
    run()
