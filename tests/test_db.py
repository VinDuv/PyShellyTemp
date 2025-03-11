"""
Database module tests
"""

from unittest.mock import call, patch, mock_open, sentinel, ANY, MagicMock, Mock
import contextlib
import dataclasses
import io
import pathlib
import sqlite3
import sys
import typing
import unittest

from pyshellytemp.db.access import Database, DBType, DBValueField, DBFKField
from pyshellytemp.db.access import DBCmpOp, DBOrder, DBQuery, DBUniqueError
from pyshellytemp.db.upgrade import DatabaseUpgrader


PATCH_UPGRADE_DIR = 'pyshellytemp.db.upgrade.DatabaseUpgrader.DB_UPGRADE_DIR'
TEST_DIR_PATH = pathlib.Path(__file__).parent / 'data' / 'db_upgrade'


class FakeLock:
    effect = None

    def __enter__(self):
        if self.__class__.effect:
            self.__class__.effect()
        return None

    def __exit__(self, _exc_type, _exc_value, _traceback):
        return


class DatabaseTests(unittest.TestCase):
    @patch('pyshellytemp.db.access.sqlite3')
    def test_db_raw(self, mock_sqlite3):
        mock_sqlite3.threadsafety = 3
        conn = mock_sqlite3.connect.return_value
        conn.__enter__.return_value = conn
        conn.execute.side_effect = [
            None, # pragma foreign_keys on
            Mock(**{'fetchone.return_value': (123,)}), # pragma user_version
            Mock(lastrowid=None),
        ]

        db = Database('/fakepath')
        db.set_db_version(123)

        db_open = mock_open()
        with patch('pyshellytemp.db.access.open', db_open):
            db.exec_raw('some sql;', ('a', 'b', 'c'))
        mock_sqlite3.connect.assert_called_with('/fakepath',
            check_same_thread=False)

        conn.execute.assert_has_calls([
            call('pragma foreign_keys = on;'),
            call('pragma user_version;'),
            call('some sql;', ('a', 'b', 'c')),
        ])

        conn.execute.reset_mock()
        conn.execute.side_effect = None
        conn.execute.return_value = sentinel.cursor
        res = db.fetch_raw('some fetch sql;', ('d', 'e', 'f'))
        conn.execute.assert_has_calls([
            call('some fetch sql;', ('d', 'e', 'f')),
        ])
        self.assertEqual(res, sentinel.cursor)

    @patch('pyshellytemp.db.access.sqlite3')
    def test_db_raw_conn(self, mock_sqlite3):
        mock_sqlite3.threadsafety = 3
        conn = mock_sqlite3.connect.return_value
        conn.__enter__.return_value = conn
        conn.execute.side_effect = [
            None, # pragma foreign_keys on
            Mock(**{'fetchone.return_value': (123,)}), # pragma user_version
            Mock(lastrowid=None),
        ]

        db = Database('/fakepath')
        db.set_db_version(123)

        with patch('pyshellytemp.db.access.open') as mock_opener:
            mock_opener.side_effect = FileNotFoundError()
            cur, target, raw_conn = db.prepare_db_upgrade()
            self.assertEqual(cur, 0)
            self.assertEqual(target, 123)
            self.assertIsNone(raw_conn)

        db_open = mock_open()
        with patch('pyshellytemp.db.access.open', db_open):
            cur, target, raw_conn = db.prepare_db_upgrade()
            self.assertEqual(cur, 123)
            self.assertEqual(target, 123)
            self.assertIs(raw_conn, conn)

    @patch('pyshellytemp.db.access.sqlite3')
    def test_db_paths(self, mock_sqlite3):
        mock_sqlite3.threadsafety = 3
        conn = mock_sqlite3.connect.return_value
        conn.__enter__.return_value = conn
        conn.execute.side_effect = [
            None, # pragma foreign_keys on
            Mock(**{'fetchone.return_value': (0,)}), # pragma user_version
            Mock(lastrowid=None),
            None, # pragma foreign_keys on
            Mock(**{'fetchone.return_value': (0,)}), # pragma user_version
            Mock(lastrowid=None),
            None, # pragma foreign_keys on
            Mock(**{'fetchone.return_value': (0,)}), # pragma user_version
            Mock(lastrowid=None),
        ]

        db = Database()
        db.set_default_db_path('/default_path')
        db.set_db_path('/set_path')

        db_open = mock_open()
        with patch('pyshellytemp.db.access.open', db_open), \
            patch('pyshellytemp.db.access.os.environ', {'DB_PATH': '/env_path'}):
            db.exec_raw('some sql;', ('a', 'b', 'c'))
        db_open.assert_called_with('/set_path', 'r+b')
        mock_sqlite3.connect.assert_called_with('/set_path',
            check_same_thread=False)

        mock_sqlite3.connect.reset_mock()
        db_open.reset_mock()

        db = Database()
        db.set_default_db_path('/default_path')

        with patch('pyshellytemp.db.access.open', db_open), \
            patch('pyshellytemp.db.access.os.environ', {'DB_PATH': '/env_path'}):
            db.exec_raw('some sql;', ('a', 'b', 'c'))
        db_open.assert_called_with('/env_path', 'r+b')
        mock_sqlite3.connect.assert_called_with('/env_path',
            check_same_thread=False)

        mock_sqlite3.connect.reset_mock()
        db_open.reset_mock()

        db = Database()
        db.set_default_db_path('/default_path')

        with patch('pyshellytemp.db.access.open', db_open), \
            patch('pyshellytemp.db.access.os.environ', {}):
            db.exec_raw('some sql;', ('a', 'b', 'c'))
        db_open.assert_called_with('/default_path', 'r+b')
        mock_sqlite3.connect.assert_called_with('/default_path',
            check_same_thread=False)

    @patch('pyshellytemp.db.access.os.unlink')
    @patch('pyshellytemp.db.access.sqlite3')
    def test_db_init(self, mock_sqlite3, mock_unlink):
        mock_sqlite3.threadsafety = 3
        conn = mock_sqlite3.connect.return_value
        conn.__enter__.return_value = conn
        conn.execute.return_value.lastrowid = 42

        # Path does not exist
        db = Database('/some_path')
        with patch('pyshellytemp.db.access.open',
            Mock(side_effect=FileNotFoundError())):
            db.init()

        mock_sqlite3.connect.assert_called_with('/some_path',
            check_same_thread=False)

        # Path already exists
        mock_sqlite3.connect.reset_mock()
        db = Database('/some_path')
        with patch('pyshellytemp.db.access.open', mock_open()):
            with self.assertRaisesRegex(SystemExit, 'The database /some_path '
                'already exists'):
                db.init()
        mock_unlink.assert_not_called()
        mock_sqlite3.connect.assert_not_called()

        hooks = Mock()

        # Path already exists, force used
        mock_sqlite3.connect.reset_mock()
        db = Database('/some_path')
        db.register_init_hook(priority=2)(hooks.prio2)
        db.register_init_hook(priority=0)(hooks.prio0)
        db.register_init_hook(priority=1)(hooks.prio1)

        with patch('pyshellytemp.db.access.open', mock_open()):
            db.init(force=True)
        mock_unlink.assert_called_once_with('/some_path')
        mock_sqlite3.connect.assert_called_with('/some_path',
            check_same_thread=False)

        hooks.assert_has_calls([
            call.prio0(db),
            call.prio1(db),
            call.prio2(db),
        ])


    @patch('pyshellytemp.db.access.threading.Lock', FakeLock)
    @patch('pyshellytemp.db.access.sqlite3')
    def test_concurrent_connect(self, mock_sqlite3):
        mock_sqlite3.threadsafety = 3

        db = Database('/fakepath')

        actual_conn = Mock()
        actual_conn.__enter__ = Mock(return_value=actual_conn)
        actual_conn.__exit__ = lambda _se, _ty, _va, _tb: None
        def set_conn():
            db._conn = actual_conn
        FakeLock.effect = set_conn

        db_open = mock_open()
        with patch('pyshellytemp.db.access.open', db_open):
            db.exec_raw('some sql;', ('a', 'b', 'c'))
        mock_sqlite3.connect.assert_not_called()

        actual_conn.execute.assert_has_calls([
            call('some sql;', ('a', 'b', 'c')),
        ])

    @patch('pyshellytemp.db.access.sqlite3')
    def test_db_errors(self, mock_sqlite3):
        # Invalid path
        mock_sqlite3.OperationalError = sqlite3.OperationalError
        db = Database()
        with self.assertRaisesRegex(SystemExit, "Invalid database path"):
            db.set_db_path("")

        with self.assertRaisesRegex(ValueError, "Invalid database version"):
            db.set_db_version(2147483648)

        with self.assertRaisesRegex(ValueError, "Invalid database version"):
            db.set_db_version(-2147483649)

        # Database does not exist
        db = Database('/some_path')
        with patch('pyshellytemp.db.access.open',
            Mock(side_effect=FileNotFoundError())):
            with self.assertRaisesRegex(SystemExit, 'has not been created yet'):
                db.exec_raw('some sql;', ('a', 'b', 'c'))
        mock_sqlite3.connect.assert_not_called()

        # Bad thread safety
        mock_sqlite3.threadsafety = 2
        with patch('pyshellytemp.db.access.open', mock_open()):
            with self.assertRaisesRegex(SystemExit, 'SQLite has insufficient '
                'thread safety guarantees'):
                db.exec_raw('some sql;', ('a', 'b', 'c'))
        mock_sqlite3.connect.assert_not_called()

        # Permission error opening database
        mock_sqlite3.threadsafety = 3
        with patch('pyshellytemp.db.access.open', Mock(side_effect=PermissionError('abcd'))):
            with self.assertRaisesRegex(SystemExit, 'Unable to access the '
                'database: abcd'):
                db.exec_raw('some sql;', ('a', 'b', 'c'))
        mock_sqlite3.connect.assert_not_called()

        # Connect error during init
        db = Database('/some_path')
        mock_sqlite3.connect.side_effect = sqlite3.OperationalError('defg')
        with patch('pyshellytemp.db.access.open',
            Mock(side_effect=FileNotFoundError())):
            with self.assertRaisesRegex(SystemExit, 'Error creating database'):
                db.init()

        # Base database version
        mock_sqlite3.connect.side_effect = None
        conn = mock_sqlite3.connect.return_value
        conn.__enter__.return_value = conn
        conn.execute.side_effect = [
            None, # pragma foreign_keys on
            Mock(**{'fetchone.return_value': (123,)}), # pragma user_version
            Mock(lastrowid=None),
        ]

        db = Database('/some_path')
        db.set_db_version(456)

        db_open = mock_open()
        with patch('pyshellytemp.db.access.open', db_open):
            with self.assertRaisesRegex(SystemExit, r"The database is at "
                r"version 123, but is expected to be at version 456\."):
                db.exec_raw('some sql;', ('a', 'b', 'c'))

    @patch('pyshellytemp.db.access.Database.exec_raw')
    def test_create_table(self, mock_exec):
        table_def = {
            'id': DBValueField(type=DBType.PKEY, nullable=False, unique=False),
            'ival': DBValueField(type=DBType.from_type(int), nullable=True,
                unique=True),
            'fval': DBValueField(type=DBType.FLOAT, nullable=False,
                unique=False),
            'sval': DBValueField(type=DBType.STR, nullable=False,
                unique=False),
            'bval': DBValueField(type=DBType.BYTES, nullable=False,
                unique=True),
            'fk1': DBFKField('other_table', 'id', nullable=False, unique=False),
            'fk2': DBFKField('another_table', 'id', nullable=True,
                unique=False),
        }

        Database().create_table('some_table', table_def)

        mock_exec.assert_called_once_with('create table some_table ('
            'id integer primary key not null, '
            'ival integer null unique, '
            'fval real not null, '
            'sval text not null, '
            'bval blob not null unique, '
            'fk1 integer not null, '
            'fk2 integer null, '
            'foreign key (fk1) references other_table (id) on delete cascade, '
            'foreign key (fk2) references another_table (id) on delete set null'
            ');')

    @patch('pyshellytemp.db.access.Database.fetch_raw')
    def test_select(self, mock_fetch):
        mock_fetch.return_value = [
            [42, 43],
            [44, 45],
        ]

        simple_req = DBQuery(table_name='some_table', filter=[], order=[])
        db = Database('/invalid')
        res = db.select(['a', 'b'], simple_req)
        res = list(list(x) for x in res)

        mock_fetch.assert_called_once_with('select a, b from some_table;', [])

        self.assertEqual(res, [
            [42, 43],
            [44, 45],
        ])

        mock_fetch.reset_mock()

        req_filter = [
            ('c', DBCmpOp.LT, 123),
            ('d', DBCmpOp.GTE, 456)
        ]

        order = [DBOrder.extract_order(x) for x in ('c', '-d', '+e')]

        complex_req = DBQuery(table_name='some_table', filter=req_filter,
            order=order, offset=42)
        res = db.select(['a', 'b'], complex_req)
        res = list(list(x) for x in res)

        mock_fetch.assert_called_once_with('select a, b from some_table '
            'where c < ? and d >= ? order by c asc, d desc, e asc limit -1 '
            'offset 42;', [123, 456])

        self.assertEqual(res, [
            [42, 43],
            [44, 45],
        ])

    @patch('pyshellytemp.db.access.Database.exec_raw')
    def test_insert(self, mock_exec):
        db = Database('/invalid')
        mock_exec.return_value = 42

        res = db.insert('some_table', {'a': 25})
        mock_exec.assert_called_once_with('insert into some_table (a) values '
            '(?);', [25])
        self.assertEqual(res, 42)

        mock_exec.reset_mock()

        mock_exec.return_value = 10
        res = db.insert('some_table', {'a': 25, 'b': 36})
        mock_exec.assert_called_once_with('insert into some_table (a, b) '
            'values (?, ?);', [25, 36])
        self.assertEqual(res, 10)

    @patch('pyshellytemp.db.access.Database.exec_raw')
    def test_update(self, mock_exec):
        db = Database('/invalid')

        res = db.update_equal('some_table', 'id', 42, {'a': 25, 'b': 36})
        mock_exec.assert_called_once_with('update some_table set a = ?, b = ? '
            'where id = ?;', [25, 36, 42])

    @patch('pyshellytemp.db.access.Database.exec_raw')
    def test_delete(self, mock_exec):
        db = Database('/invalid')

        req_filter = [
            ('c', DBCmpOp.LT, 123),
            ('d', DBCmpOp.GTE, 456)
        ]

        order = [DBOrder.extract_order(x) for x in ('c', '-d', '+e')]

        complex_req = DBQuery(table_name='some_table', filter=req_filter,
            order=order, offset=42)
        db.delete_matching(complex_req)

        mock_exec.assert_called_once_with('delete from some_table where rowid '
            'in (select rowid from some_table where c < ? and d >= ? order by '
            'c asc, d desc, e asc limit -1 offset 42);', [123, 456])

        mock_exec.reset_mock()

        db.delete_equal('some_table', 'c', 42)
        mock_exec.assert_called_once_with('delete from some_table where c = ?;',
            (42,))

    @patch('pyshellytemp.db.access.Database.exec_raw')
    def test_query_errors(self, mock_exec):
        db = Database('/invalid')
        mock_exec.side_effect = sqlite3.IntegrityError("Some error")

        with self.assertRaisesRegex(sqlite3.IntegrityError, "Some error"):
            db.insert('some_table', {'a': 25})

        mock_exec.side_effect = sqlite3.IntegrityError("UNIQUE constraint "
            "failed")
        with self.assertRaisesRegex(DBUniqueError, "UNIQUE constraint failed"):
            db.insert('some_table', {'a': 25})

    def test_cmp_op(self):
        self.assertEqual(DBCmpOp.extract_comp('some_field'),
            ('some_field', DBCmpOp.EQ))
        self.assertEqual(DBCmpOp.extract_comp('some_field__eq'),
            ('some_field', DBCmpOp.EQ))
        self.assertEqual(DBCmpOp.extract_comp('some_field__lte'),
            ('some_field', DBCmpOp.LTE))

        with self.assertRaisesRegex(ValueError, "'xx' is not a valid "
            "comparator"):
            DBCmpOp.extract_comp('some_field__xx')


@dataclasses.dataclass(frozen=True)
class FakeTableDef:
    fields: dict[str, str]
    tables: typing.ClassVar[dict[str, 'FakeTableDef']] = {}

    def get_db_fields(self):
        return self.fields

    @staticmethod
    def format_db_fields(fields):
        return ', '.join(f'{key} {value}' for key, value in fields.items())


@patch('pyshellytemp.db.upgrade.TableDef', FakeTableDef)
@patch('pyshellytemp.db.upgrade.database')
class DatabaseUpgradeTests(unittest.TestCase):
    def test_no_models(self, mock_db):
        with self.assertRaisesRegex(ValueError, "No models specified"):
            DatabaseUpgrader()

    @patch(PATCH_UPGRADE_DIR, TEST_DIR_PATH / 'single')
    def test_no_db(self, mock_db):
        mock_db.prepare_db_upgrade.return_value = 0, 123, None

        with self.assertRaisesRegex(SystemExit, "The database does not exist"):
            DatabaseUpgrader(None).do_upgrade()

    @patch(PATCH_UPGRADE_DIR, TEST_DIR_PATH / 'single')
    def test_no_upgrade_needed(self, mock_db):
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_db.prepare_db_upgrade.return_value = 123, 123, mock_conn

        stream = io.StringIO()

        with contextlib.redirect_stdout(stream):
            DatabaseUpgrader(None).do_upgrade()

        self.assertEqual(stream.getvalue(),
            "The database is already up to date.\n")

    @patch(PATCH_UPGRADE_DIR, TEST_DIR_PATH / 'no_match')
    def test_no_scripts_in_dir(self, mock_db):
        with self.assertRaisesRegex(SystemExit, "No database upgrade scripts "
            "are defined"):
            DatabaseUpgrader(None)

    @patch(PATCH_UPGRADE_DIR, TEST_DIR_PATH / 'single')
    def test_cur_version_too_old(self, mock_db):
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_db.prepare_db_upgrade.return_value = 121, 123, mock_conn

        with self.assertRaisesRegex(SystemExit, "No available upgrade scripts "
            "for starting version 121"):
            DatabaseUpgrader(None).do_upgrade()

    @patch(PATCH_UPGRADE_DIR, TEST_DIR_PATH / 'single')
    def test_target_mismatch(self, mock_db):
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_db.prepare_db_upgrade.return_value = 122, 124, mock_conn

        with self.assertRaisesRegex(SystemExit, "App target version is 124 but "
            "last script version is 123."):
            DatabaseUpgrader(None).do_upgrade()

    @patch(PATCH_UPGRADE_DIR, TEST_DIR_PATH / 'single')
    def test_single_upgrade(self, mock_db):
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_db.prepare_db_upgrade.return_value = 122, 123, mock_conn
        mock_curs = mock_conn.cursor.return_value

        FakeTableDef.tables = {
            'a': FakeTableDef({'x': 'int'}),
        }

        mock_db.format_db_fields = FakeTableDef.format_db_fields

        mock_curs.description = [
            ('a',) + (None,) * 6,
            ('b',) + (None,) * 6,
            ('c',) + (None,) * 6,
        ]

        mock_curs.execute.return_value.fetchall.side_effect = [
            [], # First script line
            [(1, 2, 3)], # Second script line
            [], # Foreign key check
        ]

        mock_curs.execute.return_value.fetchone.side_effect = [
            (123,) # user_version after upgrade
        ]

        mock_curs.__iter__.return_value = iter([
            ('a', 'CREATE TABLE a (x int)')
        ])

        with patch('pyshellytemp.db.upgrade.print') as mock_print:
            DatabaseUpgrader(None).do_upgrade()

        self.assertSequenceEqual(mock_conn.cursor.return_value.mock_calls, [
            call.execute('pragma foreign_keys=off;'),
            call.execute('begin transaction;'),
            call.execute('bl/* ; */ah "/* */\\"\\";";'),
            call.execute().fetchall(),
            call.execute(' --comment\nabc'),
            call.execute().fetchall(),
            call.execute('pragma user_version;'),
            call.execute().fetchone(),
            call.execute('pragma foreign_key_check;'),
            call.execute().fetchall(),
            call.execute("select name, sql from sqlite_schema where type "
                "= 'table';"),
            call.__iter__(),
            call.execute('commit;'),
            call.execute('pragma foreign_keys=on;'),
            call.close()
        ])

        mock_conn.assert_has_calls([
            call.__enter__(),
            call.cursor(),
        ])
        mock_conn.assert_has_calls([
           call.__exit__(None, None, None),
        ])

        self.assertSequenceEqual(mock_print.mock_calls, [
            call('* Running 123_test.sql:'),
            call('bl/* ; */ah "/* */\\"\\";";'),
            call(''),
            call(' --comment\nabc'),
            call('a              |b              |c              '),
            call('1              |2              |3              '),
            call('')
        ])

    @patch(PATCH_UPGRADE_DIR, TEST_DIR_PATH / 'single')
    def test_sql_error(self, mock_db):
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_db.prepare_db_upgrade.return_value = 122, 123, mock_conn
        mock_curs = mock_conn.cursor.return_value

        FakeTableDef.tables = {
            'a': FakeTableDef({'x': 'int'}),
        }

        mock_db.format_db_fields = FakeTableDef.format_db_fields

        mock_curs.description = [
            ('a',) + (None,) * 6,
            ('b',) + (None,) * 6,
            ('c',) + (None,) * 6,
        ]

        mock_curs.execute.return_value.fetchall.side_effect = [
            sqlite3.OperationalError("Some error")
        ]

        with self.assertRaisesRegex(SystemExit, "SQL error: Some error"):
            with patch('pyshellytemp.db.upgrade.print') as mock_print:
                DatabaseUpgrader(None).do_upgrade()

        self.assertSequenceEqual(mock_conn.cursor.return_value.mock_calls, [
            call.execute('pragma foreign_keys=off;'),
            call.execute('begin transaction;'),
            call.execute('bl/* ; */ah "/* */\\"\\";";'),
            call.execute().fetchall(),
            call.close()
        ])

        mock_conn.assert_has_calls([
            call.__enter__(),
            call.cursor(),
        ])
        mock_conn.assert_has_calls([
           call.__exit__(SystemExit, ANY, ANY),
        ])

        self.assertSequenceEqual(mock_print.mock_calls, [
            call('* Running 123_test.sql:'),
            call('bl/* ; */ah "/* */\\"\\";";'),
        ])

    @patch(PATCH_UPGRADE_DIR, TEST_DIR_PATH / 'single')
    def test_target_version_not_changed(self, mock_db):
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_db.prepare_db_upgrade.return_value = 122, 123, mock_conn
        mock_curs = mock_conn.cursor.return_value

        FakeTableDef.tables = {
            'a': FakeTableDef({'x': 'int'}),
        }

        mock_db.format_db_fields = FakeTableDef.format_db_fields

        mock_curs.description = [
            ('a',) + (None,) * 6,
            ('b',) + (None,) * 6,
            ('c',) + (None,) * 6,
        ]

        mock_curs.execute.return_value.fetchall.side_effect = [
            [], # First script line
            [(1, 2, 3)], # Second script line
            [], # Foreign key check
        ]

        mock_curs.execute.return_value.fetchone.side_effect = [
            (122,) # user_version after upgrade
        ]

        with self.assertRaisesRegex(SystemExit, "The script did not correctly "
            "update the target version"):
            with patch('pyshellytemp.db.upgrade.print') as mock_print:
                DatabaseUpgrader(None).do_upgrade()

        self.assertSequenceEqual(mock_conn.cursor.return_value.mock_calls, [
            call.execute('pragma foreign_keys=off;'),
            call.execute('begin transaction;'),
            call.execute('bl/* ; */ah "/* */\\"\\";";'),
            call.execute().fetchall(),
            call.execute(' --comment\nabc'),
            call.execute().fetchall(),
            call.execute('pragma user_version;'),
            call.execute().fetchone(),
            call.close()
        ])

        mock_conn.assert_has_calls([
            call.__enter__(),
            call.cursor(),
        ])
        mock_conn.assert_has_calls([
           call.__exit__(SystemExit, ANY, ANY),
        ])

        self.assertSequenceEqual(mock_print.mock_calls, [
            call('* Running 123_test.sql:'),
            call('bl/* ; */ah "/* */\\"\\";";'),
            call(''),
            call(' --comment\nabc'),
            call('a              |b              |c              '),
            call('1              |2              |3              '),
            call('')
        ])

    @patch(PATCH_UPGRADE_DIR, TEST_DIR_PATH / 'single')
    def test_fk_error(self, mock_db):
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_db.prepare_db_upgrade.return_value = 122, 123, mock_conn
        mock_curs = mock_conn.cursor.return_value

        FakeTableDef.tables = {
            'a': FakeTableDef({'x': 'int'}),
        }

        mock_db.format_db_fields = FakeTableDef.format_db_fields

        mock_curs.description = [
            ('a',) + (None,) * 6,
            ('b',) + (None,) * 6,
            ('c',) + (None,) * 6,
        ]

        mock_curs.execute.return_value.fetchall.side_effect = [
            [], # First script line
            [(1, 2, 3)], # Second script line
            [
                ('a', 1, 'b', 0), # Foreign key check
            ],
        ]

        mock_curs.execute.return_value.fetchone.side_effect = [
            (123,) # user_version after upgrade
        ]

        mock_curs.__iter__.return_value = iter([
            ('a', 'CREATE TABLE a (x int)')
        ])

        with self.assertRaisesRegex(SystemExit, '1'):
            with patch('pyshellytemp.db.upgrade.print') as mock_print:
                DatabaseUpgrader(None).do_upgrade()

        self.assertSequenceEqual(mock_conn.cursor.return_value.mock_calls, [
            call.execute('pragma foreign_keys=off;'),
            call.execute('begin transaction;'),
            call.execute('bl/* ; */ah "/* */\\"\\";";'),
            call.execute().fetchall(),
            call.execute(' --comment\nabc'),
            call.execute().fetchall(),
            call.execute('pragma user_version;'),
            call.execute().fetchone(),
            call.execute('pragma foreign_key_check;'),
            call.execute().fetchall(),
            call.close()
        ])

        mock_conn.assert_has_calls([
            call.__enter__(),
            call.cursor(),
        ])
        mock_conn.assert_has_calls([
           call.__exit__(SystemExit, ANY, ANY),
        ])

        self.assertSequenceEqual(mock_print.mock_calls, [
            call('* Running 123_test.sql:'),
            call('bl/* ; */ah "/* */\\"\\";";'),
            call(''),
            call(' --comment\nabc'),
            call('a              |b              |c              '),
            call('1              |2              |3              '),
            call(''),
            call('Foreign key errors after script execution:', file=sys.stderr),
            call('Table a row 1: target b', file=sys.stderr),
        ])

    @patch(PATCH_UPGRADE_DIR, TEST_DIR_PATH / 'multi')
    def test_multi_schema_table_missing(self, mock_db):
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_db.prepare_db_upgrade.return_value = 122, 124, mock_conn
        mock_curs = mock_conn.cursor.return_value

        FakeTableDef.tables = {
            'a': FakeTableDef({'x': 'int'}),
        }

        mock_db.format_db_fields = FakeTableDef.format_db_fields

        mock_curs.description = NotImplemented

        mock_curs.execute.return_value.fetchall.side_effect = [
            [], # First script line
            [], # Foreign key check
            [], # Second script line
            [], # Foreign key check
        ]

        mock_curs.execute.return_value.fetchone.side_effect = [
            (123,), # user_version after upgrade1
            (124,), # user_version after upgrade2
        ]

        # Table 'a' missing
        mock_curs.__iter__.return_value = iter([])

        with self.assertRaisesRegex(SystemExit, "Schema error: table 'a' is "
            "missing"):
            with patch('pyshellytemp.db.upgrade.print') as mock_print:
                DatabaseUpgrader(None).do_upgrade()

        self.assertSequenceEqual(mock_conn.cursor.return_value.mock_calls, [
            call.execute('pragma foreign_keys=off;'),
            call.execute('begin transaction;'),
            call.execute('script1;'),
            call.execute().fetchall(),
            call.execute('pragma user_version;'),
            call.execute().fetchone(),
            call.execute('pragma foreign_key_check;'),
            call.execute().fetchall(),
            call.execute('script2'),
            call.execute().fetchall(),
            call.execute('pragma user_version;'),
            call.execute().fetchone(),
            call.execute('pragma foreign_key_check;'),
            call.execute().fetchall(),
            call.execute("select name, sql from sqlite_schema where type "
                "= 'table';"),
            call.__iter__(),
            call.close()
        ])

        mock_conn.assert_has_calls([
            call.__enter__(),
            call.cursor(),
        ])
        mock_conn.assert_has_calls([
           call.__exit__(SystemExit, ANY, ANY),
        ])

        self.assertSequenceEqual(mock_print.mock_calls, [
            call('* Running 123_script1.sql:'),
            call('script1;'),
            call(''),
            call('* Running 124_script2.sql:'),
            call('script2'),
            call(''),
        ])

    @patch(PATCH_UPGRADE_DIR, TEST_DIR_PATH / 'multi')
    def test_multi_schema_mismatch(self, mock_db):
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_db.prepare_db_upgrade.return_value = 122, 124, mock_conn
        mock_curs = mock_conn.cursor.return_value

        FakeTableDef.tables = {
            'a': FakeTableDef({'x': 'int'}),
        }

        mock_db.format_db_fields = FakeTableDef.format_db_fields

        mock_curs.description = NotImplemented

        mock_curs.execute.return_value.fetchall.side_effect = [
            [], # First script line
            [], # Foreign key check
            [], # Second script line
            [], # Foreign key check
        ]

        mock_curs.execute.return_value.fetchone.side_effect = [
            (123,), # user_version after upgrade1
            (124,), # user_version after upgrade2
        ]

        mock_curs.__iter__.return_value = iter([
            ('a', 'CREATE TABLE a (x real)')
        ])

        with self.assertRaisesRegex(SystemExit, "1"):
            with patch('pyshellytemp.db.upgrade.print') as mock_print:
                DatabaseUpgrader(None).do_upgrade()

        self.assertSequenceEqual(mock_conn.cursor.return_value.mock_calls, [
            call.execute('pragma foreign_keys=off;'),
            call.execute('begin transaction;'),
            call.execute('script1;'),
            call.execute().fetchall(),
            call.execute('pragma user_version;'),
            call.execute().fetchone(),
            call.execute('pragma foreign_key_check;'),
            call.execute().fetchall(),
            call.execute('script2'),
            call.execute().fetchall(),
            call.execute('pragma user_version;'),
            call.execute().fetchone(),
            call.execute('pragma foreign_key_check;'),
            call.execute().fetchall(),
            call.execute("select name, sql from sqlite_schema where type "
                "= 'table';"),
            call.__iter__(),
            call.close()
        ])

        mock_conn.assert_has_calls([
            call.__enter__(),
            call.cursor(),
        ])
        mock_conn.assert_has_calls([
           call.__exit__(SystemExit, ANY, ANY),
        ])

        self.assertSequenceEqual(mock_print.mock_calls, [
            call('* Running 123_script1.sql:'),
            call('script1;'),
            call(''),
            call('* Running 124_script2.sql:'),
            call('script2'),
            call(''),
            call("Schema error: table 'a' definition do not match",
                file=sys.stderr),
            call('Expected: x int', file=sys.stderr),
            call('Actual:   x real', file=sys.stderr),
        ])

    def test_authorizer(self, _):
        # pragma foreign_keys (note that the capitalisation used in the script
        # is passed to the authorizer)
        with patch('pyshellytemp.db.upgrade.print') as mock_print:
            res = DatabaseUpgrader._authorizer(sqlite3.SQLITE_PRAGMA,
                'fOrEigN_kEyS', NotImplemented, NotImplemented, NotImplemented)
        self.assertIs(res, sqlite3.SQLITE_IGNORE)
        mock_print.assert_called_once_with(" (ignored, done by upgrader)")

        # pragma user_version (same thing)
        with patch('pyshellytemp.db.upgrade.print') as mock_print:
            res = DatabaseUpgrader._authorizer(sqlite3.SQLITE_PRAGMA,
                'user_version', NotImplemented, NotImplemented, NotImplemented)
        self.assertIs(res, sqlite3.SQLITE_OK)
        mock_print.assert_not_called()

        # Other pragmas are denied
        with patch('pyshellytemp.db.upgrade.print') as mock_print:
            res = DatabaseUpgrader._authorizer(sqlite3.SQLITE_PRAGMA,
                'fullfsync', NotImplemented, NotImplemented, NotImplemented)
        self.assertIs(res, sqlite3.SQLITE_DENY)
        mock_print.assert_called_once_with("Unknown pragma 'fullfsync'",
            file=sys.stderr)

        # begin/commit (the name is normalized)
        with patch('pyshellytemp.db.upgrade.print') as mock_print:
            res = DatabaseUpgrader._authorizer(sqlite3.SQLITE_TRANSACTION,
                'BEGIN', NotImplemented, NotImplemented, NotImplemented)
        self.assertIs(res, sqlite3.SQLITE_IGNORE)
        mock_print.assert_called_once_with(" (ignored, done by upgrader)")

        # Other transactions are denied
        with patch('pyshellytemp.db.upgrade.print') as mock_print:
            res = DatabaseUpgrader._authorizer(sqlite3.SQLITE_TRANSACTION,
                'ROLLBACK', NotImplemented, NotImplemented, NotImplemented)
        self.assertIs(res, sqlite3.SQLITE_DENY)
        mock_print.assert_called_once_with("Unknown transaction 'ROLLBACK'",
            file=sys.stderr)

        # Other operations are allowed
        with patch('pyshellytemp.db.upgrade.print') as mock_print:
            res = DatabaseUpgrader._authorizer(sqlite3.SQLITE_ALTER_TABLE,
                'a', NotImplemented, NotImplemented, NotImplemented)
        self.assertIs(res, sqlite3.SQLITE_OK)
        mock_print.assert_not_called()
