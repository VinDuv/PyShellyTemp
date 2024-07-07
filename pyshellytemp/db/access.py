"""
Database access layer
Simplifies the interaction between Python code and the database.
Currently only supports SQLite3 databases.
"""

import contextlib
import enum
import itertools
import logging
import os
import sqlite3
import sys
import threading
import typing


LOGGER = logging.getLogger(__name__)

DBValue: typing.TypeAlias = int | float | str | bytes | None
DBParam: typing.TypeAlias = list[DBValue] | tuple[DBValue, ...]


class DBType(enum.Enum):
    """
    Type that can be stored in the database.
    """

    PKEY = 'integer primary key'
    INT = 'integer'
    FLOAT = 'real'
    STR = 'text'
    BYTES = 'blob'

    @classmethod
    def from_type(cls, type_var: type[int|float|str|bytes]) -> typing.Self:
        """
        Returns a DBType from a native database type.
        """

        return cls[type_var.__name__.upper()]


class DBValueField(typing.NamedTuple):
    """
    Defines the representation of a value field in the database.
    """

    type: DBType
    unique: bool
    nullable: bool

    @staticmethod
    def get_fk_def() -> None:
        """
        Value fields have no foreign key definition
        """

        return None


class DBFKField(typing.NamedTuple):
    """
    Defines the representation of a foreign key field in the database.
    """

    ref_table: str
    ref_field: str
    unique: bool
    nullable: bool

    @property
    def type(self) -> DBType:
        """
        Foreign key fields are always integers
        """

        return DBType.INT

    def get_fk_def(self) -> tuple[str, str, str]:
        """
        Return the foreign key definition of the field:
         - Referenced table
         - Referenced field in the table
         - ON DELETE behavior
        """

        if self.nullable:
            on_delete = "set null"
        else:
            on_delete = "cascade"

        return (self.ref_table, self.ref_field, on_delete)


class DBCmpOp(enum.Enum):
    """
    Database comparison operator
    """

    EQ = 'eq'
    LT = 'lt'
    LTE = 'lte'
    GT = 'gt'
    GTE = 'gte'

    @classmethod
    def extract_comp(cls, value: str) -> tuple[str, 'DBCmpOp']:
        """
        Takes a “where” specifier, formed by a column name, a double underscore,
        and a comparator, and returns the column bame and the comparator as
        a DBCmpOp. If no double underscore is present, the EQ comparator is
        assumed.
        """

        col_name, found, raw_cmp = value.rpartition('__')
        if not found:
            return value, cls.EQ

        try:
            cmp = cls(raw_cmp)
        except ValueError:
            raise ValueError(f"Unable to parse where expression {value!r}: "
                f"{raw_cmp!r} is not a valid comparator") from None

        return col_name, cmp


class DBOrder(enum.Enum):
    """
    Database select/delete order
    """

    ASC = 'asc'
    DESC = 'desc'

    @classmethod
    def extract_order(cls, value: str) -> tuple[str, 'DBOrder']:
        """
        Takes a column name possibly prefixed by a + or -, and returns the
        column name without the prefix and the corresponding order field.
        """

        first_char = value[0:1]
        if first_char == '+':
            return (value[1:], cls.ASC)

        if first_char == '-':
            return (value[1:], cls.DESC)

        return (value, cls.ASC)


class DBUniqueError(Exception):
    """
    Raised when a UNIQUE constraint fails during an insert or update.
    """

    @classmethod
    @contextlib.contextmanager
    def check(cls) -> typing.Generator[None, None, None]:
        """
        Checks for SQL errors during the execution of the context manager. If
        an error is raised due to a UNIQUE constraint failing, raises a
        DBUniqueError. In all other cases, processes normally.
        """

        try:
            yield
        except sqlite3.IntegrityError as err:
            msg = err.args[0]

            if msg.startswith('UNIQUE constraint failed'):
                raise cls(msg) from None

            raise


DBField: typing.TypeAlias = DBValueField | DBFKField
StrIt: typing.TypeAlias = typing.Iterable[str]
SelectRow: typing.TypeAlias = typing.Iterable[DBValue]
SelectRes: typing.TypeAlias = typing.Iterable[SelectRow]
DBHook: typing.TypeAlias = typing.Callable[['Database'], None]

class DBQuery(typing.NamedTuple):
    """
    Parameters to a SELECT or DELETE request.
    """

    table_name: str
    filter: typing.Iterable[tuple[str, DBCmpOp, DBValue]]
    order: typing.Iterable[tuple[str, DBOrder]]
    offset: int = -1
    max_count: int = -1


class Database:
    """
    Handles access to a SQLite3 database.
    """

    class Separator:
        """
        Used to build queries. The first call to get() returns the head value,
        the following calls returns the separator value.
        """

        def __init__(self, head: str = '', separator: str = ', '):
            self._head = head
            self._separator = separator
            self._first = True

        def get(self) -> str:
            """
            Returns the separator value
            """

            if self._first:
                self._first = False
                return self._head

            return self._separator

        def __repr__(self) -> str:
            return f"Separator({self._head!r}, {self._separator}!r)"

    CMP_STR = {
        DBCmpOp.EQ: ' = ?',
        DBCmpOp.LT: ' < ?',
        DBCmpOp.LTE: ' <= ?',
        DBCmpOp.GT: ' > ?',
        DBCmpOp.GTE: ' >= ?',
    }

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path: str | None = db_path
        self._default_db_path: str | None = None
        self._conn: sqlite3.Connection | None = None
        self._init_lock = threading.Lock()
        self._init_hooks: list[tuple[int, DBHook]] = []

    def create_table(self, name: str, fields: dict[str, DBField]) -> None:
        """
        Create a table in the database with the specified fields.
        """

        self.exec_raw("".join(self._create_table_parts(name, fields)))

    def select(self, col_names: StrIt, query: DBQuery) -> SelectRes:
        """
        Performs a SELECT on the database with the specified query parameters.
        Returns an generator over the rows found; each row is itself a
        generator yielding the database values.
        """

        params: list[DBValue] = []
        query_str = "".join(self._select_parts(col_names, query, params))
        yield from self.fetch_raw(query_str, params)

    def insert(self, table_name: str, values: dict[str, DBValue]) -> int:
        """
        Inserts a new value in the specified table.
        Returns the inserted row ID.
        """

        params: list[DBValue] = []
        query_str = "".join(self._insert_parts(table_name, values, params))

        with DBUniqueError.check():
            return self.exec_raw(query_str, params)

    def update_equal(self, table_name: str, col_name: str, col_val: DBValue,
        new_values: dict[str, DBValue]) -> None:
        """
        Update rows where col_name = col_val to put the new values.
        """

        params: list[DBValue] = []
        query_str = "".join(self._update_equal_parts(table_name, (col_name,
            col_val), new_values, params))

        with DBUniqueError.check():
            self.exec_raw(query_str, params)

    def delete_equal(self, table_name: str, col_name: str,
        value: DBValue) -> None:
        """
        Delete rows whose col_name’s value matches the provided value.
        Faster than delete_matching for simple delete queries.
        """

        self.exec_raw(f'delete from {table_name} where {col_name} = ?;',
            (value,))

    def delete_matching(self, query: DBQuery) -> None:
        """
        Delete rows matching the query.
        """

        params: list[DBValue] = []
        query_str = "".join(self._delete_parts(query, params))
        self.exec_raw(query_str, params)

    def exec_raw(self, query: str, parameters: DBParam=()) -> int:
        """
        Executes a single raw SQL operation on the database.
        If an INSERT or REPLACE query is executed, the row ID (if any) is
        returned.
        This should only be used for SQL queries that to do not return results.
        """

        with self._get_connection() as conn:
            LOGGER.debug("Exec: %s %r", query, parameters)
            cursor = conn.execute(query, parameters)
            return cursor.lastrowid or -1

    def fetch_raw(self, query: str, parameters: DBParam=()) -> sqlite3.Cursor:
        """
        Executes a raw query operation on the database. The resulting cursor is
        returned so the results can be examined.
        """

        LOGGER.debug("Fetch: %s %r", query, parameters)

        return self._get_connection().execute(query, parameters)

    def set_default_db_path(self, path: str) -> None:
        """
        Sets the default database path. Should always be called early at
        application startup. This path can be overriden by set_db_path or
        the DB_PATH environnement variable.
        """

        if self._db_path is not None:
            raise AssertionError(f"Database path already set to "
                f"{self._db_path}")

        if self._default_db_path is not None:
            raise AssertionError(f"Default database path already set to"
                f"{self._default_db_path}")

        self._default_db_path = path

    def set_db_path(self, path: str) -> None:
        """
        Sets the path to the database, overriding the default path or the path
        set by the DB_PATH environnement variable.
        Can only be called before exec() or fetch() is used.
        """

        if self._db_path is not None:
            raise AssertionError(f"Database path already set to "
                f"{self._db_path}")

        if not path:
            sys.exit(f"Invalid database path {path}")

        self._db_path = path

    def init(self, *, force: bool=False) -> None:
        """
        Initializes a new database. If force is True, any existing database will
        be erased.
        """

        with self._init_lock:
            db_path = self._get_db_path()

            if self._conn is not None:
                raise AssertionError("Database is already loaded, cannot "
                    "re-init")

            if self._check_db_file_exists(db_path):
                if force:
                    os.unlink(db_path)
                else:
                    sys.exit(f"The database {db_path} already exists, "
                        "use the force parameter to force its "
                        "re-initialization.")

            try:
                self._conn = self._connect_to_db(db_path)
            except sqlite3.OperationalError as err:
                sys.exit(f"Error creating database {db_path}: {err}. "
                    f"Check that the database path is valid.")

            for _, hook in sorted(self._init_hooks, key=lambda item: item[0]):
                hook(self)

    def register_init_hook(self, *, priority: int) -> \
        typing.Callable[[DBHook], DBHook]:
        """
        Used as a decorator to register a database init hook, with the specified
        priority.
        Priority 0 is reserved for the hook that create the tables.
        """

        def _inner(hook: DBHook) -> DBHook:
            self._init_hooks.append((priority, hook))

            return hook

        return _inner

    def _get_connection(self) -> sqlite3.Connection:
        """
        Connects to the database if necessary, and returns the connection.
        """

        if self._conn is not None:
            return self._conn

        # The init lock is mainly there to prevent two threads from creating
        # a database connection.

        with self._init_lock:
            db_path = self._get_db_path()

            if self._conn is not None:
                return self._conn

            if not self._check_db_file_exists(db_path):
                sys.exit(f"The database at {db_path} has not been "
                    f"created yet. Use init_db to create it, or set another "
                    f"database path with the DB_PATH environment variable.")

            self._conn = self._connect_to_db(db_path)

            return self._conn

    def _get_db_path(self) -> str:
        """
        Gets the path to the database. Takes into account the default path,
        the DB_PATH environnement variable, and set_db_path override.
        """

        if self._db_path is not None:
            return self._db_path

        self._db_path = os.getenv('DB_PATH', '') or self._default_db_path

        if self._db_path is None:
            raise AssertionError("No database path was set")

        return self._db_path

    @classmethod
    def _create_table_parts(cls, name: str, fields: dict[str, DBField]) -> \
        StrIt:
        """
        Yields parts of a create table SQL statement that creates the specified
        table.
        """

        yield from ('create table ', name, ' (')

        sep = cls.Separator()

        for field_name, field_def in fields.items():
            yield from (sep.get(), field_name, ' ', field_def.type.value)
            if field_def.nullable:
                yield ' null'
            else:
                yield ' not null'

            if field_def.unique:
                yield ' unique'

        for field_name, field_def in fields.items():
            fk_def = field_def.get_fk_def()
            if fk_def is None:
                continue

            ref_table, ref_field, on_delete = fk_def
            yield from (sep.get(), 'foreign key (', field_name, ') references ',
                ref_table, ' (', ref_field, ') on delete ', on_delete)

        yield ');'

    @classmethod
    def _select_parts(cls, col_names: StrIt, query: DBQuery,
        params: list[DBValue]) -> StrIt:
        """
        Yields part of a SQL statement that selects the specified columns in the
        specified query.
        Also builds the required parameter list.
        """

        yield 'select '

        sep = cls.Separator()
        for col_name in col_names:
            yield sep.get()
            yield col_name

        yield from cls._query_parts(query, params)

        yield ';'

    @classmethod
    def _insert_parts(cls, table_name: str, value_dict: dict[str, DBValue],
        params: list[DBValue]) -> StrIt:
        """
        Yields part of a SQL statement that selects the specified columns in the
        specified query.
        Also builds the required parameter list (must be empty at first).
        """

        yield from ('insert into ', table_name, ' (')

        sep = cls.Separator()
        for col_name, col_val in value_dict.items():
            yield sep.get()
            yield col_name
            params.append(col_val)
        yield ') values (?'

        if not params:
            raise AssertionError("No values provided")

        yield from itertools.repeat(', ?', len(params) - 1)

        yield ');'

    @classmethod
    def _update_equal_parts(cls, table_name: str, ref: tuple[str, DBValue],
        new_values: dict[str, DBValue], params: list[DBValue]) -> StrIt:

        col_name, col_val = ref

        yield from ('update ', table_name, ' set ')
        sep = cls.Separator()
        for updated_col_name, updated_col_val in new_values.items():
            yield sep.get()
            yield updated_col_name
            yield ' = ?'
            params.append(updated_col_val)

        yield from (' where ', col_name, ' = ?')
        params.append(col_val)
        yield ';'

    @classmethod
    def _delete_parts(cls, query: DBQuery, params: list[DBValue]) -> StrIt:
        """
        Yields part of a delete SQL statement that delete the rows matching the
        specified query.
        Also builds the required parameter list.
        """

        # SQLite does not support limit/offset in DELETE queries, so use a
        # delete in select construct

        yield from ('delete from ', query.table_name,
            ' where rowid in (select rowid')

        yield from cls._query_parts(query, params)

        yield ');'

    @classmethod
    def _query_parts(cls, query: DBQuery, params: list[DBValue]) -> StrIt:
        """
        Yields parts of a SELECT/DELETE statement:
         - from <table name>
         - where <filters>
         - order by <columns>
         - limit/offset
        Also builds the required parameter list.
        """

        yield from (' from ', query.table_name)

        sep = cls.Separator(' where ', ' and ')
        for col, cmp_op, db_value in query.filter:
            yield sep.get()
            yield col
            yield cls.CMP_STR[cmp_op]
            params.append(db_value)

        sep = cls.Separator(' order by ')
        for order_col, order_val in query.order:
            yield sep.get()
            yield from (order_col, ' ', order_val.value)

        if query.max_count >= 0 or query.offset >= 0:
            # LIMIT is required even if just an offset is specified
            yield from (' limit ', str(query.max_count))

        if query.offset >= 0:
            yield from (' offset ', str(query.offset))

    @staticmethod
    def _connect_to_db(path: str) -> sqlite3.Connection:
        """
        Opens the database, creating it if needed.
        Returns the created connection.
        """

        if sqlite3.threadsafety != 3:
            sys.exit("SQLite has insufficient thread safety guarantees.")

        conn = sqlite3.connect(path, check_same_thread=False)
        conn.execute('pragma foreign_keys = on;')

        return conn

    @staticmethod
    def _check_db_file_exists(path: str) -> bool:
        """
        Checks that the database file exists.
        """

        try:
            with open(path, 'r+b'):
                return True
        except FileNotFoundError:
            return False
        except OSError as err:
            sys.exit(f"Unable to access the database: {err}")


database = Database()
