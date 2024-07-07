"""
Handling of database fields (i.e. columns) from Python definition to database
conversion.
"""

import abc
import dataclasses
import datetime
import enum
import typing

from .access import DBField, DBFKField, DBType, DBValue, DBValueField


# MARK: Properties that can be set on a database object’s fields
T = typing.TypeVar('T')


@dataclasses.dataclass
class RawField(typing.Generic[T]):
    """
    Attribute of a DBObject field.
    """

    factory: typing.Optional[typing.Callable[[], T]]
    unique: bool


class Unset(enum.Enum):
    """
    Enum used to create a unique marker value, used to differentiate an unset
    default to a default of None.
    """

    UNSET = 'unset'

# The field() function returns a Field object, but we need to pretend it returns
# a type equivalent to the field it’s used with, so type checking works. This is
# done with @typing.overload.
# The first overload indicates that field() or field(unique=True) has type Any,
# so x: int = field(unique=True) is accepted.
# The second overload indicates that field(default=<some val>) returns the type
# of <some val>. This allows the type checker to accept
# x: int = field(default=3) but to reject x: int = field(default="a").
# The third overload works the same but for default factories.
# Using overloads also prevents the user from passing both default and
# default_factory (as long as type checking is used).

@typing.overload
def field(*, is_unique: bool = False) -> typing.Any:
    raise NotImplementedError("Overload stub")


@typing.overload
def field(*, default: T | Unset = Unset.UNSET, is_unique: bool = False) -> T:
    raise NotImplementedError("Overload stub")


@typing.overload
def field(*, default_factory: typing.Optional[typing.Callable[[], T]] = None,
    is_unique: bool = False) -> T:
    raise NotImplementedError("Overload stub")


def field(*, default: typing.Any = Unset.UNSET,
    default_factory: typing.Optional[typing.Callable[[], typing.Any]] = None,
    is_unique: bool = False) -> typing.Any:
    """
    Instantiates field attributes.
    """

    if default is not Unset.UNSET:
        if default_factory is not None:
            raise ValueError("Cannot use default and default_factory "
                "simulatenously")

        return RawField(factory=lambda: default, unique=is_unique)

    if default_factory is not None:
        return RawField(factory=default_factory, unique=is_unique)

    return RawField(factory=None, unique=is_unique)

def unique() -> typing.Any:
    """
    Instantiates a field with the unique marker.
    """

    return RawField(factory=None, unique=True)


# MARK: Python to database type conversion
DatabaseTypes: typing.TypeAlias = int | float | str | bytes
DbTy = typing.TypeVar('DbTy', bound=DatabaseTypes)
PyTy = typing.TypeVar('PyTy')


class DBValueConverter(typing.Generic[PyTy, DbTy], typing.Protocol):
    """
    A class that contains methods converting a given Python value to a database
    value.
    """

    @staticmethod
    def py_to_db(py_val: PyTy) -> DbTy:
        """
        Converts a Python value to its corresponding database value.
        """

    @staticmethod
    def db_to_py(db_val: DbTy) -> PyTy:
        """
        Converts a database value to its corresponding Python value.
        """

class DBSelfConverter(typing.Generic[DbTy], typing.Protocol):
    """
    A class that contains method to convert instances of that class to a
    database value, and back.
    """

    @classmethod
    def py_to_db(cls, py_val: typing.Self) -> DbTy:
        """
        Converts a Python value to its corresponding database value.
        """

    @classmethod
    def db_to_py(cls, db_val: DbTy) -> typing.Self:
        """
        Converts a database value to its corresponding Python value.
        """


def reg_db_conv(py_type: type[PyTy], db_type: type[DbTy]) \
    -> typing.Callable[[type[DBValueConverter[PyTy, DbTy]]], \
    type[DBValueConverter[PyTy, DbTy]]]:
    """
    Decorator that register a class to convert values between a Python type
    and a database type. The Python and database types must be given to the
    decorator before being applied to the class.
    """

    def _inner(converter: type[DBValueConverter[PyTy, DbTy]]) -> \
        type[DBValueConverter[PyTy, DbTy]]:
        ORMValueField.register_converter(py_type, db_type, converter)
        return converter

    return _inner


def reg_db_type(db_type: type[DbTy]) -> \
    typing.Callable[[type[DBSelfConverter[DbTy]]], type[DBSelfConverter[DbTy]]]:
    """
    Decorator that registers a class whose instances can be converted to a
    database type and back. The database type must be given to the decorator
    before being applied to the class.
    """

    def _inner(converter: type[DBSelfConverter[DbTy]]) -> \
        type[DBSelfConverter[DbTy]]:
        ORMValueField.register_converter(converter, db_type, converter)
        return converter

    return _inner


# Built-in converters; to avoid ordering issues, they are registered directly
# in ORMField without using the decorators.


class IdentityConverter:
    """
    Identity converter: Used for types that can be stored natively in the
    database.
    """

    @staticmethod
    def py_to_db(py_val: T) -> T:
        """
        Returns the value unchanged since no conversion is needed.
        """
        return py_val

    @staticmethod
    def db_to_py(db_val: T) -> T:
        """
        Returns the value unchanged since no conversion is needed.
        """
        return db_val


class DateTimeConverter:
    """
    Date/Time converter: used to store a datetime object in the database.
    """

    @staticmethod
    def py_to_db(py_val: datetime.datetime) -> float:
        """
        Converts a datetime to its floating-point timestamp representation
        """
        return py_val.timestamp()

    @staticmethod
    def db_to_py(db_val: float) -> datetime.datetime:
        """
        Convert a float timestamp to a datetime
        """
        return datetime.datetime.fromtimestamp(db_val)


class BoolConverter:
    """
    Date/Time converter: used to store a bool object in the database.
    """

    @staticmethod
    def py_to_db(py_val: bool) -> int:
        """
        Converts a boolean to its integer representation
        """
        return int(py_val)

    @staticmethod
    def db_to_py(db_val: int) -> bool:
        """
        Convert an integer to a boolean
        """
        return bool(db_val)


# MARK: ORM field definitions

class DBObjectBase:
    """
    Base class for database objects. Avoids a circular dependency between
    the the DBObject code, that uses a field table with fields, and the fields,
    that use DBObject for foreign key reference. Only parts of DBObject that
    relate to DBObject foreign key support are put in this class.
    """

    # This abstract class is not defined with abc.ABC because this causes a
    # conflicts with DBObject’s metaclass

    __slots__ = ['id']

    # Database unique identifier of the object
    id: int

    @classmethod
    def db_get_table_name(cls) -> str:
        """
        Returns the name of the database table that holds the object.
        """

        raise NotImplementedError()

    @classmethod
    def get_one(cls, **kwargs: typing.Any) -> typing.Self:
        """
        Finds one object from the table, given the provided search arguments.
        Raises a KeyError if no items matched, ValueError if more than one item
        matched.
        """

        raise NotImplementedError()

    def db_has_id(self) -> bool:
        """
        Returns True if the object has a stable database ID.
        """

        raise NotImplementedError()


# Note: These classes could be made generic to improve type checking (at
# internal level; user-facing code is hopefully correctly type-checked). This is
# tricky because of the way nullability is handled; if the type is nullable,
# the factory returns an optional T, but if it’s not it returns a T. There are
# also some issues recovering the database type for value fields.

AnyFactory: typing.TypeAlias = typing.Optional[typing.Callable[[], typing.Any]]


@dataclasses.dataclass(frozen=True)
class ORMField(abc.ABC):
    """
    Base class for an ORM field. Represents a datase column in a table.
    """

    # Name of the column in the database; not necessarily identical to the
    # name used on the Python side.
    db_name: str

    # Indicates if the value is unique in the column
    unique: bool

    # Indicates if None can be stored in the column
    nullable: bool

    # The default value factory of the field, if any
    factory: AnyFactory

    @abc.abstractmethod
    def get_db_field(self) -> DBField:
        """
        Returns the definition of the field at database level.
        """

    def get_fk_type(self) -> type[DBObjectBase]:
        """
        Returns the type of the foreign object. Only valid for foreign keys.
        """

        raise AssertionError("get_fk_type called on non-FK type")

    def convert_to_db(self, field_name: str, value: typing.Any) -> DBValue:
        """
        Converts a Python value to the database value. Raises an error if the
        value is None but should not (according to the field’s nullable
        property) or is the wrong type.
        """

        if value is None:
            if not self.nullable:
                raise ValueError(f"Field {field_name!r} is not nullable; "
                    f"cannot be set to None")

            return None

        return self._convert_to_db(field_name, value)

    def convert_to_py_and_set(self, field_name: str, value: DBValue,
        target_obj: DBObjectBase, fk_ids: dict[str, int]) -> None:
        """
        Converts the database value to a Python and sets it as an attribute on
        the target object. If the field is a foreign key field, the foreign key
        ID is set on the fk_ids field (if it is None, the None is set on the
        target object directly).
        """

        if value is None:
            # Set the corresponding object attribute to None. This works for
            # regular values as well as foreign key targets (no need to lazy
            # load a foreign key that is None…)

            assert self.nullable

            object.__setattr__(target_obj, field_name, None)

            return

        self._convert_to_py_and_set(field_name, value, target_obj, fk_ids)

    @staticmethod
    def from_definition(name: str, py_type: type, nullable: bool,
        attrs: RawField[typing.Any]) -> 'ORMField':
        """
        Returns an instance of ORMField from the specified definition
        """

        if issubclass(py_type, DBObjectBase):
            return ORMFKField.create(name, py_type, nullable, attrs)

        return ORMValueField.create(name, py_type, nullable, attrs)

    @abc.abstractmethod
    def _convert_to_db(self, field_name: str, value: typing.Any) -> DBValue:
        """
        Validates the type of the value and converts it to its database format.
        """

    @abc.abstractmethod
    def _convert_to_py_and_set(self, field_name: str, value: DBValue,
        target_obj: DBObjectBase, fk_ids: dict[str, int]) -> None:
        """
        Converts a non-None database value to a Python and sets it as an
        attribute on the target object or its fk_ids field.
        """


@dataclasses.dataclass(frozen=True)
class ORMFloatValueField(ORMField):
    """
    Represent a database column in a table that points to a float value.
    This is a special case of ORMValueField for floats, so it allows the
    int -> float implicit conversion.
    """

    def get_db_field(self) -> DBField:
        return DBValueField(type=DBType.FLOAT, nullable=self.nullable,
            unique=self.unique)

    def _convert_to_db(self, field_name: str, value: typing.Any) -> DBValue:
        if not isinstance(value, (int, float)):
            raise ValueError(f"Field {field_name!r} can only accept values of "
                f"type int or float")

        return value

    def _convert_to_py_and_set(self, field_name: str, value: DBValue,
        target_obj: DBObjectBase, fk_ids: dict[str, int]) -> None:

        assert isinstance(value, (int, float))

        object.__setattr__(target_obj, field_name, value)


@dataclasses.dataclass(frozen=True)
class ORMValueField(ORMField):
    """
    Represents a database column in a table that points to a value.
    """

    # The Python type stored in the field
    py_type: type

    # The corresponding database type
    db_type: type[DatabaseTypes]

    # The value converter class (from py_type to db_type and back)
    converter: type[DBValueConverter[typing.Any, typing.Any]]

    # Associates a Python type with a database type and a class that converts
    # between the two.
    _registered_types: typing.ClassVar[dict[type, tuple[type,
        type[DBValueConverter[typing.Any, typing.Any]]]]] = {
        bool: (int, BoolConverter),
        int: (int, IdentityConverter),
        float: (float, IdentityConverter),
        str: (str, IdentityConverter),
        bytes: (bytes, IdentityConverter),
        datetime.datetime: (float, DateTimeConverter),
    }

    @classmethod
    def create(cls, name: str, py_type: type, nullable: bool,
        attrs: RawField[typing.Any]) -> ORMField:
        """
        Creates a value field from the provided values.
        """

        factory = attrs.factory
        is_unique = attrs.unique

        if py_type is float:
            return ORMFloatValueField(db_name=name, unique=is_unique,
                nullable=nullable, factory=factory)

        try:
            db_type, converter = cls._registered_types[py_type]
        except KeyError:
            raise TypeError(f"No database converter registered for Python "
                f"type {py_type.__name__!r}") from None

        return cls(db_name=name, unique=is_unique, nullable=nullable,
            py_type=py_type, db_type=db_type, converter=converter,
            factory=factory)

    @classmethod
    def register_converter(cls, py_type: type[PyTy], db_type: type[DbTy],
        converter: type[DBValueConverter[PyTy, DbTy]]) -> None:
        """
        Registers a type converter class to be used in fields.
        The Python and database types should match the ones used by the
        converter.
        """

        if py_type in cls._registered_types:
            raise ValueError(f"A converter is already registered for Python "
                f"type {py_type.__name__!r}")

        cls._registered_types[py_type] = (db_type, converter)

    def get_db_field(self) -> DBField:
        return DBValueField(type=DBType.from_type(self.db_type),
            nullable=self.nullable, unique=self.unique)

    def _convert_to_db(self, field_name: str, value: typing.Any) -> DBValue:
        if self.py_type is not type(value):
            raise ValueError(f"Field {field_name!r} can only accept values of "
                f"type {self.py_type.__name__!r}")

        db_value = self.converter.py_to_db(value)

        assert isinstance(db_value, self.db_type), (db_value, self.db_type)

        # Redundant with the previous check but keeps mypy happy
        assert isinstance(db_value, (int, float, str, bytes))

        return db_value

    def _convert_to_py_and_set(self, field_name: str, value: DBValue,
        target_obj: DBObjectBase, fk_ids: dict[str, int]) -> None:

        py_value = self.converter.db_to_py(value)
        assert self.py_type is type(py_value), (py_value, self.py_type)

        object.__setattr__(target_obj, field_name, py_value)


@dataclasses.dataclass(frozen=True)
class ORMIDField(ORMValueField):
    """
    Represents the identifier column of a table.
    """

    _instance: typing.ClassVar[typing.Optional['ORMIDField']] = None

    @classmethod
    def get(cls) -> 'ORMIDField':
        """
        Returns the singleton ORMIDField instance.
        """

        if cls._instance is not None:
            return cls._instance

        cls._instance = cls(db_name='id', unique=True, nullable=False,
            py_type=int, db_type=int, converter=IdentityConverter, factory=None)

        return cls._instance

    def get_db_field(self) -> DBField:
        return DBValueField(type=DBType.PKEY, nullable=False, unique=False)


@dataclasses.dataclass(frozen=True)
class ORMFKField(ORMField):
    """
    Represents a database column in a table that points to another table.
    """

    # The target type of the foreign key (DBObject subclass)
    target: type[DBObjectBase]

    @classmethod
    def create(cls, name: str, target: type['DBObjectBase'], nullable: bool,
        attrs: RawField[typing.Any]) -> typing.Self:
        """
        Creates a value field from the provided values.
        """

        factory = attrs.factory
        is_unique = attrs.unique

        # Add an _id suffix to the column name in the database, since the
        # foreign key column stores the ID of the target object.
        return cls(db_name=f'{name}_id', unique=is_unique, nullable=nullable,
            target=target, factory=factory)

    def get_db_field(self) -> DBField:
        return DBFKField(ref_table=self.target.db_get_table_name(),
            ref_field='id', nullable=self.nullable, unique=self.unique)

    def get_fk_type(self) -> type[DBObjectBase]:
        return self.target

    def _convert_to_db(self, field_name: str, value: typing.Any) -> DBValue:
        if not isinstance(value, self.target):
            raise ValueError(f"Field {field_name!r} can only accept values of "
                f"type {self.target.__name__!r}")

        if not value.db_has_id():
            raise ValueError(f"Field {field_name!r} can only accept objects "
                f"that have been saved to the database")

        return value.id

    def _convert_to_py_and_set(self, field_name: str, value: DBValue,
        target_obj: DBObjectBase, fk_ids: dict[str, int]) -> None:

        assert isinstance(value, int)

        fk_ids[field_name] = value
