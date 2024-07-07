"""
Module entry point; contain admin tools
"""

import argparse
import getpass

# pylint: disable=W0611
from . import app, models
from .db import database
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


if __name__ == '__main__':
    run()
