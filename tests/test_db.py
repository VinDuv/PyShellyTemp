"""
Database module tests
"""

from unittest.mock import call, patch, mock_open, sentinel, Mock
import unittest
import sqlite3

from pyshellytemp.db.access import Database, DBType, DBValueField, DBFKField
from pyshellytemp.db.access import DBCmpOp, DBOrder, DBQuery, DBUniqueError


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
        conn.execute.return_value.lastrowid = 42

        db = Database('/fakepath')

        db_open = mock_open()
        with patch('pyshellytemp.db.access.open', db_open):
            db.exec_raw('some sql;', ('a', 'b', 'c'))
        mock_sqlite3.connect.assert_called_with('/fakepath',
            check_same_thread=False)

        conn.execute.assert_has_calls([
            call('pragma foreign_keys = on;'),
            call('some sql;', ('a', 'b', 'c')),
        ])

        conn.execute.reset_mock()
        conn.execute.return_value = sentinel.cursor
        res = db.fetch_raw('some fetch sql;', ('d', 'e', 'f'))
        conn.execute.assert_has_calls([
            call('some fetch sql;', ('d', 'e', 'f')),
        ])
        self.assertEqual(res, sentinel.cursor)

    @patch('pyshellytemp.db.access.sqlite3')
    def test_db_paths(self, mock_sqlite3):
        mock_sqlite3.threadsafety = 3
        conn = mock_sqlite3.connect.return_value
        conn.__enter__.return_value = conn
        conn.execute.return_value.lastrowid = 42

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
