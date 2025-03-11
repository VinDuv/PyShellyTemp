"""
Database upgrade handling
"""

import pathlib
import re
import sqlite3
import sys
import types
import typing

from .access import database
from .orm import TableDef


class DatabaseUpgrader:
    """
    Handles the upgrade of the database.
    """

    DB_UPGRADE_DIR = pathlib.Path(__file__).parent.parent / 'db_upgrade'
    DB_UPGRADE_FILE_RE = re.compile(r'^(\d+)_[a-z0-9_]+\.sql$')

    class DatabaseScript(typing.NamedTuple):
        """
        A database script.
        """

        target_ver: int
        name: str
        statements: list[str]

    def __init__(self, *models: types.ModuleType) -> None:
        """
        Initializes the database upgrader.
        Takes in parameters the application modules that contains models. They
        are not actually used, but passing them here makes sure that that the
        models are loaded.
        """

        if not models:
            raise ValueError("No models specified")

        self._db_scripts = self._get_db_scripts()

    def do_upgrade(self) -> None:
        """
        Performs the database upgrade.
        """

        cur_ver, target_ver, conn = database.prepare_db_upgrade()
        if conn is None:
            sys.exit("The database does not exist. Check its path.")

        with conn:
            self._do_upgrade_on(conn, cur_ver, target_ver)

    def _do_upgrade_on(self, conn: sqlite3.Connection, cur_ver: int,
        target_ver: int) -> None:
        """
        Perform the database upgrade on the specified connection.
        """

        if cur_ver == target_ver:
            print("The database is already up to date.")
            return

        min_upgrade_ver = self._db_scripts[0].target_ver - 1
        script_target_ver = self._db_scripts[-1].target_ver

        if cur_ver < min_upgrade_ver:
            sys.exit(f"No available upgrade scripts for starting version "
                f"{cur_ver}.")

        if target_ver != script_target_ver:
            sys.exit(f"App target version is {target_ver} but last script "
                f"version is {script_target_ver}.")

        curs = conn.cursor()
        try:
            curs.execute('pragma foreign_keys=off;')
            curs.execute('begin transaction;')

            conn.set_authorizer(self._authorizer)

            for db_script in self._db_scripts:
                if db_script.target_ver <= cur_ver:
                    continue

                self._run_script(db_script, curs)

            self._check_schema(curs)

            conn.set_authorizer(None)
            curs.execute('commit;')
            curs.execute('pragma foreign_keys=on;')

        finally:
            conn.set_authorizer(None)

            curs.close()

    def __repr__(self) -> str:
        return "<DatabaseUpgrader>"

    def _run_script(self, db_script: DatabaseScript, curs: sqlite3.Cursor) -> \
        None:
        """
        Runs the specified database script on the specified connection cursor.
        The changes are rolled back if an error occurs or the database version
        does not match the expected value at the end.
        """

        print(f"* Running {db_script.name}:")
        for statement in db_script.statements:
            print(statement)
            try:
                res = curs.execute(statement).fetchall()
            except sqlite3.OperationalError as err:
                sys.exit(f"SQL error: {err}")

            if not res:
                print("")
                continue

            print("|".join(name[0].ljust(15) for name in curs.description))
            for row in res:
                print("|".join(str(value).ljust(15) for value in row))

            print("")

        new_ver = curs.execute('pragma user_version;').fetchone()[0]
        if new_ver != db_script.target_ver:
            sys.exit("The script did not correctly update the target version")

        fk_errors = curs.execute('pragma foreign_key_check;').fetchall()
        if fk_errors:
            print("Foreign key errors after script execution:", file=sys.stderr)
            for source, row, target, _ in fk_errors:
                print(f"Table {source} row {row}: target {target}",
                    file=sys.stderr)
            sys.exit(1)

    def _check_schema(self, curs: sqlite3.Cursor) -> None:
        """
        Checks that the schema of specified database connection matches
        what is expected. Prints errors and exits iff the check fails.
        """

        db_table_def: dict[str, str] = {}
        curs.execute('select name, sql from sqlite_schema where '
            'type = \'table\';')

        for table_name, sql in curs:
            def_start = sql.index('(')
            assert sql[-1] == ')'
            db_table_def[table_name] = sql[def_start + 1:-1]

        for table_name, table_def in TableDef.tables.items():
            table_fields = table_def.get_db_fields()
            expected_def = ''.join(database.format_db_fields(table_fields))
            actual_def = db_table_def.get(table_name)

            if actual_def is None:
                sys.exit(f"Schema error: table {table_name!r} is missing")

            if expected_def == actual_def:
                continue

            print(f"Schema error: table {table_name!r} definition do not match",
                file=sys.stderr)

            print(f"Expected: {expected_def}", file=sys.stderr)
            print(f"Actual:   {actual_def}", file=sys.stderr)
            sys.exit(1)

    @staticmethod
    def _authorizer(oper: int, arg: str | None, *_args: str | None) -> int:
        """
        Performs some checks on the operations done by the scripts.
        - Ignore pragma foreign_keys, allow user_version and foreign_key_check,
          deny others
        - Ignore BEGIN/COMMIT, deny other transactions
        """

        if oper == sqlite3.SQLITE_PRAGMA:
            assert arg is not None

            arg = arg.lower()

            if arg == 'foreign_keys':
                print(" (ignored, done by upgrader)")
                return sqlite3.SQLITE_IGNORE

            if arg in {'user_version', 'foreign_key_check'}:
                return sqlite3.SQLITE_OK

            print(f"Unknown pragma {arg!r}", file=sys.stderr)
            return sqlite3.SQLITE_DENY

        if oper == sqlite3.SQLITE_TRANSACTION:
            assert arg is not None

            if arg in {'BEGIN', 'COMMIT'}:
                print(" (ignored, done by upgrader)")
                return sqlite3.SQLITE_IGNORE

            print(f"Unknown transaction {arg!r}", file=sys.stderr)
            return sqlite3.SQLITE_DENY

        return sqlite3.SQLITE_OK

    @classmethod
    def _get_db_scripts(cls) -> list[DatabaseScript]:
        """
        Returns a sorted list of the database scripts.
        """

        scripts: list[DatabaseUpgrader.DatabaseScript] = []
        versions: set[int] = set()

        for item in cls.DB_UPGRADE_DIR.iterdir():
            match = cls.DB_UPGRADE_FILE_RE.match(item.name)

            if not item.is_file() or not match:
                continue

            target_ver = int(match.group(1))
            statements = cls._parse_script(item)

            script = cls.DatabaseScript(target_ver, item.name, statements)

            versions.add(target_ver)
            scripts.append(script)

        scripts.sort(key=lambda script: script.target_ver)
        count = len(scripts)

        if not count:
            sys.exit("No database upgrade scripts are defined.")

        min_ver = scripts[0].target_ver
        max_ver = scripts[-1].target_ver

        assert count == len(versions) == (max_ver + 1 - min_ver), ("Upgrade "
            "scripts are not contiguous or have duplicates")

        return scripts

    @staticmethod
    def _parse_script(path: pathlib.Path) -> list[str]:
        """
        Parse a SQLite script into statements that can be executed one by one.
        Comments are left in the statements; this may return comments composed
        of comments only, but this is accepted by SQLite.
        """

        statements: list[str] = []

        full_text = path.read_text(encoding='utf-8')
        st_start = 0
        st_partial_end = 0
        while True:
            try:
                st_partial_end = full_text.index(';', st_partial_end) + 1
            except ValueError:
                end = full_text[st_start:].strip('\n')
                if end:
                    statements.append(end)
                break

            maybe_statement = full_text[st_start:st_partial_end]
            if sqlite3.complete_statement(maybe_statement):
                statements.append(maybe_statement.strip('\n'))
                st_start = st_partial_end

        return statements
