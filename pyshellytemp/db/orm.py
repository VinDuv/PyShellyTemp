"""
Object Relational Mapper
"""

import dataclasses
import itertools
import types
import typing

from .access import DBCmpOp, DBField, DBOrder, DBValue, DBQuery, DBUniqueError
from .access import Database, database
from .fields import DBObjectBase, RawField, ORMField, ORMIDField
from .fields import field, unique


T = typing.TypeVar('T')
AnyDict: typing.TypeAlias = dict[str, typing.Any]
ModDict: typing.TypeAlias = dict[str, DBValue]
DBObjTy = typing.TypeVar('DBObjTy', bound='DBObject')
RawFieldAttrsDict: typing.TypeAlias = dict[str, RawField[typing.Any]]


@dataclasses.dataclass(slots=True)
class DBObjectProps:
    """
    Extra properties stored by a DBObject for proper interfacing with the
    database.
    """

    # Actual ID of the object in the database, negative if not saved yet
    # Can be different from db_obj.id if the user is changing the object ID
    db_id: int

    # Foreign key IDs of the object, loaded from the database. Used to load
    # the target object when the user accesses it from the first time.
    # Indexed by the Python field name.
    fk_ids: dict[str, int]

    # Database values of the fields that were modified since the last save.
    # Indexed by the database field name.
    modified: ModDict


@dataclasses.dataclass(frozen=True)
class TableDef(typing.Generic[DBObjTy]):
    """
    Definition of a database table.
    """

    # Registered tables. Used for duplicate detection and initial table
    # creation
    tables: typing.ClassVar[dict[str, 'TableDef[typing.Any]']] = {}

     # Table name
    name: str

    # Table fields
    fields: dict[str, ORMField]

    # Name of the database columns, in the same order as fields
    db_cols: list[str]

    # Target object class
    obj_class: type[DBObjTy]

    # Raw field information, before the fields are determined
    raw_field_attrs: RawFieldAttrsDict

    # True iff keyword-only DBObject (parameters with default values can be
    # specified in any order in the class definition, but the constructor will
    # only accept keyword arguments)
    kw_only: bool

    def init_obj(self, obj: DBObjTy, init_args: tuple[typing.Any, ...],
        init_kwargs: AnyDict) -> None:
        """
        Initializes a newly created object with provided values, using the
        defaults for the values not provided.
        The init_kwargs dictionary is consumed by the operation.
        """

        self._setup_if_needed()

        modified: ModDict = {}
        props = DBObjectProps(db_id=-1, fk_ids={}, modified=modified)

        object.__setattr__(obj, '_db_props', props)

        # Convert positional arguments to keyword arguments
        if init_args:
            if self.kw_only:
                raise TypeError(f"{self.obj_class.__name__} only accepts "
                    f"keyword arguments")

            # init_args starts with the first attribute defined in the class
            # definition; 'id' is not included.
            max_args = len(self.fields) - 1
            if len(init_args) > max_args:
                raise TypeError(f"{self.obj_class.__name__} got too many "
                    f"positional arguments (max {max_args})")

            arg_names = itertools.islice(self.fields.keys(), 1, None)

            for key, value in zip(arg_names, init_args):
                if key in init_kwargs:
                    raise TypeError(f"{self.obj_class.__name__} got multiple "
                        f"values for argument {key!r}")

                init_kwargs[key] = value

        for field_name, field_obj in self.fields.items():
            try:
                field_py_val = init_kwargs.pop(field_name)
            except KeyError:
                if field_name == 'id':
                    # The user is allowed to not set the ID, so it is
                    # automatically assigned by the database.
                    continue

                factory = field_obj.factory
                if factory is None:
                    raise TypeError(f"Missing argument {field_name!r} to "
                        f"initialize {self.obj_class.__name__} instance; no "
                        f"default value available") from None

                field_py_val = factory()

            field_db_val = field_obj.convert_to_db(field_name, field_py_val)

            object.__setattr__(obj, field_name, field_py_val)
            modified[field_obj.db_name] = field_db_val

        if init_kwargs:
            key, _ = init_kwargs.popitem()

            raise TypeError(f"{self.obj_class.__name__} got an unexpected "
                f"keyword argument {key!r}")

    def init_empty_obj(self, obj: DBObjTy) -> None:
        """
        Initializes a newly created object with the default values.
        """

        self._setup_if_needed()

        modified: ModDict = {}
        props = DBObjectProps(db_id=-1, fk_ids={}, modified=modified)

        object.__setattr__(obj, '_db_props', props)

        for field_name, field_obj in self.fields.items():
            factory = field_obj.factory
            if factory is None:
                # No default value
                continue

            field_py_val: typing.Any = factory()
            field_db_val = field_obj.convert_to_db(field_name, field_py_val)

            object.__setattr__(obj, field_name, field_py_val)
            modified[field_obj.db_name] = field_db_val

    def save_obj(self, obj: DBObjTy) -> None:
        """
        Saves the object to the database (creating it if it does not exist).
        """

        props = self._get_props(obj)
        to_create = props.db_id < 0
        modified = props.modified
        new_db_id: typing.Any = None

        if not modified and not to_create:
            # Nothing to do
            return

        if to_create:
            # Check that all fields got a value
            for field_name, field_obj in self.fields.items():
                if field_obj.db_name not in modified and field_name != 'id':
                    raise ValueError(f"Unable to save object: No value set "
                        f"for field {field_name!r}")

            # Note that database.insert will return the insert ID even if the
            # id column was explicitly set
            try:
                new_db_id = database.insert(self.name, modified)
            except DBUniqueError as err:
                raise self.obj_class.AlreadyExists(str(err)) from err
            object.__setattr__(obj, 'id', new_db_id)
        else:
            database.update_equal(self.name, 'id', props.db_id, modified)
            new_db_id = modified.get('id') # None if not modified

        if new_db_id is not None:
            props.db_id = new_db_id
        modified.clear()

    def handle_obj_assign(self, obj: DBObjTy, key: str, val: typing.Any) \
        -> None:
        """
        Handles the assignment of a new value to an object’s attribute by
        updating its modification dictionary. The object still has to update
        its internal dictionary.
        """

        if key == 'id' and val < 0:
            raise ValueError("Cannot set a negative database ID")

        props = self._get_props(obj)
        modified = props.modified
        field_obj = self._get_field(key)

        field_db_val = field_obj.convert_to_db(key, val)
        modified[field_obj.db_name] = field_db_val

    def delete_obj(self, obj: DBObjTy) -> None:
        """
        Deletes an object from the database via its dictionary.
        """

        props = self._get_props(obj)

        if props.modified:
            raise ValueError("Cannot delete a modified object")

        assert props.db_id >= 0

        database.delete_equal(self.name, 'id', props.db_id)

        object.__delattr__(obj, '_db_props')

    def get_matching(self, py_filter: AnyDict, order: list[str], limit: int,
        offset: int) -> typing.Iterable[DBObjTy]:
        """
        Searches the database for items matching the filter. Basic interval
        filters are supported (x__lt gives <, x_gte gives >=, etc).

        Yields each resulting item.
        """

        self._setup_if_needed()

        query = self._build_query(py_filter, order, limit, offset)
        for row in database.select(self.db_cols, query):
            yield self._obj_from_db(row)

    def count_matching(self, py_filter: AnyDict, order: list[str], limit: int,
        offset: int) -> int:
        """
        Count the items matching the filter in the database. Supports the same
        options as get_matching.
        """

        self._setup_if_needed()

        query = self._build_query(py_filter, order, limit, offset)
        res_row = next(iter(database.select(('count(*)',), query)))
        value = next(iter(res_row))
        assert isinstance(value, int), repr(value)
        return value

    def delete_matching(self, py_filter: AnyDict, order: list[str], limit: int,
        offset: int) -> None:
        """
        Deletes items matching the filter from the database. The parameter are
        the same as get_all.
        Note: the order parameter is only significant if the limit/offset
        parameters are used.
        """

        self._setup_if_needed()

        query = self._build_query(py_filter, order, limit, offset)
        database.delete_matching(query)

    def resolve_fk(self, obj: DBObjTy, field_name: str) -> DBObjectBase | None:
        """
        Attempts to resolve a foreign key with the given field name.
        Once the foreign key is resolved, it it set on the target object so
        future accesses will not hit the database.
        Returns None if field_name does not corresponds to a field name.
        """

        try:
            field_obj = self.fields[field_name]
        except KeyError:
            return None

        props = self._get_props(obj)

        fk_obj = field_obj.get_fk_type().get_one(id=props.fk_ids[field_name])

        object.__setattr__(obj, field_name, fk_obj)

        return fk_obj

    @classmethod
    def check_has_id(cls, obj: DBObjTy) -> bool:
        """
        Checks if the specified object indicates that it has a stable ID in the
        database.
        """

        props: DBObjectProps = object.__getattribute__(obj, '_db_props')
        _  = cls

        return props.db_id >= 0 and 'id' not in props.modified

    def get_db_fields(self) -> dict[str, DBField]:
        """
        Generates the database definition of the table fields.
        """

        res: dict[str, DBField] = {}

        self._setup_if_needed()

        for field_obj in self.fields.values():
            res[field_obj.db_name] = field_obj.get_db_field()

        return res

    def get_obj_repr(self, obj: DBObjTy) -> str:
        """
        Get the representation of an instance of a database object.
        """

        attrs = ", ".join(self._get_obj_repr(obj))

        return f"{self.obj_class.__name__}({attrs})"

    @classmethod
    def prepare_class_dict(cls, class_dict: AnyDict, kw_only: bool) -> \
        RawFieldAttrsDict:
        """
        Prepares the class dictionary for a would-be DBObject subclass:
         - Checks that the definitions do not use 3 names
         - Identify defined fields that will become database fields (all
           lowercase, not starting with _). Other fields will be considered
           class attributes
         - Set up __slots__ to contain the database fields
         - Extract the field values (“... = <value>”) from the class dictionary
           that correspond to database fields. They will conflict with __slots__
           if let in place. Instead, convert them to RawField and return them
           in a dictionary that will be used later to create the table.
        """

        annotations = class_dict['__annotations__']
        for reserved_name in ('id', '_table_def', '_db_props'):
            if reserved_name in annotations or reserved_name in class_dict:
                raise ValueError(f"Reserved name {reserved_name!r} cannot be "
                    f"used in DBObject subclasses")

        unset_field = field()

        raw_field_attrs: RawFieldAttrsDict = {}

        has_default = False

        for attr_name in annotations:
            if not cls._is_db_attr(attr_name):
                # Ignored attribute
                continue

            # This will be an object attribute; determine its RawField.
            assoc_value = class_dict.pop(attr_name, unset_field)
            if isinstance(assoc_value, RawField):
                # = field(...) => used directly
                raw_field_attr = assoc_value
            else:
                # = <default value> => Wrap default value
                raw_field_attr = field(default=assoc_value)

            raw_field_attrs[attr_name] = raw_field_attr

            if raw_field_attr.factory is None:
                # The field has no default, check if the previous one had one
                # (unless kw_only is active)
                if has_default and not kw_only:
                    raise TypeError(f'non-default argument {attr_name!r} '
                        f'follows default argument')
            else:
                has_default = True

        # Put all object attributes in __slots__.
        class_dict['__slots__'] = list(raw_field_attrs.keys())

        return raw_field_attrs

    @classmethod
    def create(cls, target_type: type[DBObjTy], table_name: str,
        raw_field_attrs: RawFieldAttrsDict, kw_only: bool) -> typing.Self:
        """
        Creates a table definition for the specified DBObject subclass.
        field_attrs contains the values provided in the class definition for
        database fields (lowercase not starting with _).
        The class annotations are not yet resolved, this will be done by
        _setup_if_needed.
        """

        other_table = cls.tables.get(table_name)

        if other_table is not None:
            raise ValueError(f"Table name {table_name!r} already used for type "
                f"{other_table.obj_class.__name__!r}")

        table = cls(table_name, {}, [], target_type, raw_field_attrs, kw_only)

        cls.tables[table_name] = table

        return table

    def _setup_if_needed(self) -> None:
        """
        Resolves the raw field info and fills up the table fields. Does nothing
        if the field info has already been resolved.
        """

        if self.fields:
            return

        self.fields['id'] = ORMIDField.get()
        self.db_cols.append('id')

        # Note that this returns the annotations from parent classes,
        # so it contains 'id' and '_table_def' are returned
        annotations = typing.get_type_hints(self.obj_class)

        db_col_names: dict[str, str] = {}

        class_name = self.obj_class.__name__

        for name, raw_type in annotations.items():
            if name == 'id' or not self._is_db_attr(name):
                # Ignore id since it’s already defined as a primary key field
                # above, and ignore non-database attributes
                continue

            nullable, field_type = self._extract_nullable(self.obj_class,
                name, raw_type)

            attr = self.raw_field_attrs.pop(name)

            try:
                field_obj = ORMField.from_definition(name, field_type, nullable,
                    attr)
            except TypeError as err:
                raise TypeError(f"class {class_name}: {str(err)}") from err

            db_name = field_obj.db_name

            other_field = db_col_names.get(db_name)

            if other_field is not None:
                raise ValueError(f"{class_name}: Fields {name!r} and "
                    f"{other_field!r} have the same database field name "
                    f"{db_name!r}. Rename one of them to avoid the conflict.")

            db_col_names[db_name] = name

            self.fields[name] = field_obj
            self.db_cols.append(db_name)

        assert not self.raw_field_attrs, repr(self.raw_field_attrs)

    def _build_query(self, py_filter: AnyDict, order: list[str], limit: int,
        offset: int) -> DBQuery:
        """
        Build a database query from the provided parameters.
        """

        return DBQuery(self.name, self._build_filter(py_filter),
            self._build_order(order), offset, limit)

    def _build_filter(self, py_filter: AnyDict) -> typing.Iterable[tuple[str,
        DBCmpOp, DBValue]]:
        """
        Takes a dictionary of filter definitions (like field_lt=5) and convert
        each one into a database column name, comparison operator, and database
        value.
        """

        for raw_def, py_value in py_filter.items():
            field_name, comp_op = DBCmpOp.extract_comp(raw_def)
            field_obj = self._get_field(field_name)
            db_value = field_obj.convert_to_db(field_name, py_value)

            yield (field_obj.db_name, comp_op, db_value)

    def _build_order(self, order: list[str]) -> typing.Iterable[tuple[str,
        DBOrder]]:
        """
        Takes a list of order definitions (like +field1) and converts each one
        into a database column name and and order definition.
        """

        for raw_def in order:
            field_name, order_value = DBOrder.extract_order(raw_def)
            field_obj = self._get_field(field_name)

            yield (field_obj.db_name, order_value)

    def _obj_from_db(self, row: typing.Iterable[DBValue]) -> DBObjTy:
        """
        Creates an object from a database row. The database values order must
        match the fields dictionary order.
        """

        obj = object.__new__(self.obj_class)

        fk_ids: dict[str, int] = {}

        for (field_name, field_obj), db_value in zip(self.fields.items(), row):
            field_obj.convert_to_py_and_set(field_name, db_value, obj, fk_ids)

        props = DBObjectProps(db_id=obj.id, fk_ids=fk_ids, modified={})

        object.__setattr__(obj, '_db_props', props)

        return obj

    def _get_field(self, name: str) -> ORMField:
        """
        Get the field with the given name. Raises an AttributeError if no field
        of that name exists.
        """

        try:
            return self.fields[name]
        except KeyError:
            raise AttributeError(f"{self.obj_class.__name__!r} object has no "
                f"attribute {name!r}") from None

    def _get_obj_repr(self, obj: DBObjTy) -> typing.Iterable[str]:
        """
        Yields each attribute of the object, in key=value notation.
        Missing keys will have the value <missing>.
        Non-resolved foreign keys will have the value [class].get_one(id=[ID]).
        If the object was deleted, yields only <deleted>.
        """

        try:
            props: DBObjectProps = object.__getattribute__(obj, '_db_props')
        except AttributeError:
            yield '<deleted>'
            return

        for field_name, field_obj in self.fields.items():
            try:
                value_repr = repr(object.__getattribute__(obj, field_name))
            except AttributeError:
                fk_id = props.fk_ids.get(field_name, None)
                if fk_id is None:
                    value_repr = '<missing>'
                else:
                    type_name = field_obj.get_fk_type().__name__
                    value_repr = f'{type_name}.get_one(id={fk_id})'

            yield f'{field_name}={value_repr}'

    @staticmethod
    def _is_db_attr(attr_name: str) -> bool:
        """
        Indicate if an attribute name is a database field name.
        """

        return attr_name == attr_name.lower() and not attr_name.startswith('_')

    @classmethod
    def _extract_nullable(cls, enclosing_class: type, field_name: str,
        raw_type: type) -> \
        tuple[bool, type]:
        """
        Converts a type (from a resolved type annotation) into a boolean
        indicating if the target type is optional, and the type with its
        optionality removed.
        int -> false, int
        int | None -> true, int
        typing.Optional[Blah] -> true, Blah
        """

        # Get the X part of X[Y, Z] notation
        origin = typing.get_origin(raw_type)

        if origin in {typing.Union, types.UnionType}:
            # raw_type: typing.Union[something, None] => origin is typing.Union
            # raw_type: typing.Optional[something] => origin is typing.Union
            # raw_type: something | None => origin is types.UnionType

            # Extract the [Y, Z]. One of them should be NoneType.
            actual_types = set(typing.get_args(raw_type))
            actual_types.discard(type(None))

            if len(actual_types) != 1:
                raise ValueError(f"The type of field {field_name!r} in "
                    f"{enclosing_class.__name__!r} cannot be a union of types "
                    f"(only an optional)")

            is_opt = True
            field_type: type = actual_types.pop()

        elif origin is None:
            # Not a parametrized type, so it’s not optional.
            assert isinstance(raw_type, type), f"{raw_type!r} is not a type"

            is_opt = False
            field_type = raw_type

        else:
            # Other parametrized type, not handled
            raise ValueError(f"Field {field_name!r} in "
                f"{enclosing_class.__name__!r} cannot have a parametrized type")

        assert not isinstance(field_type, str), f"{field_name} is {raw_type}"

        return is_opt, field_type

    @staticmethod
    def _get_props(obj: DBObjTy) -> DBObjectProps:
        """
        Gets the property object of the specified DBObjectInstance.
        If the object was deleted and no longer has a property object, raises
        an appropriate error.
        """

        try:
            props: DBObjectProps = object.__getattribute__(obj, '_db_props')
        except AttributeError:
            raise ValueError("This object was deleted from the database and "
                "can no longer be used") from None

        return props

    @database.register_init_hook(priority=0)
    @staticmethod
    def _create_tables(db: Database) -> None:
        """
        Creates the tables when the database is initialized.
        """

        for table_name, table_def in TableDef.tables.items():
            db.create_table(table_name, table_def.get_db_fields())


@dataclasses.dataclass(frozen=True)
class Query(typing.Iterable[DBObjTy]):
    """
    Represents a database query. This is returned by DBObject.get_all(); the
    query can be further refined by where(), order_by(), [:] (to set limits),
    and can then either be selected by iterating on it, or deleted by calling
    .delete().
    """

    target_def: TableDef[DBObjTy]

    where: AnyDict

    order: list[str] = dataclasses.field(default_factory=list)

    limit: int = -1
    offset: int = -1

    def filter(self, **added_filter: typing.Any) -> 'Query[DBObjTy]':
        """
        Further constraints the query’s filter.
        """

        common = self.where.keys() & added_filter.keys()

        if common:
            name = common.pop()
            raise ValueError(f"This query is already filtered using {name!r}")

        new_where = self.where.copy()
        new_where.update(added_filter)

        return Query(self.target_def, new_where, self.order, self.limit,
            self.offset)

    def order_by(self, *added_order: str) -> 'Query[DBObjTy]':
        """
        Adds column ordering to the query.
        """

        new_order = self.order.copy()
        new_order.extend(added_order)

        return Query(self.target_def, self.where, new_order, self.limit,
            self.offset)

    def __getitem__(self, key: slice) -> 'Query[DBObjTy]':
        """
        Used to limit the number of items returned by the query, or to start
        returning it from a certain point.
        """

        if not isinstance(key, slice) or key.step is not None:
            raise ValueError("Continuous integer slice [start:end] expected")

        if self.limit >= 0 or self.offset >= 0:
            raise ValueError("Query limits already set")

        if key.start is None:
            new_offset = 0

        elif not isinstance(key.start, int):
            raise TypeError(f"{key.start!r}: integer expected")

        elif key.start < 0:
            raise ValueError("Cannot get the end part of the query, reverse "
                "the ordering and get the begin part instead")

        else:
            new_offset = key.start

        if key.stop is None:
            new_limit = -1

        elif not isinstance(key.stop, int):
            raise TypeError(f"{key.stop!r}: integer expected")

        elif key.stop < 0:
            raise ValueError("Cannot exclude a specified number of end items, "
                "reverse the ordering and set a start offset instead")

        elif key.stop <= new_offset:
            # Dubious...
            new_limit = 0

        else:
            new_limit = key.stop - new_offset

        return Query(self.target_def, self.where, self.order, new_limit,
            new_offset)

    def __iter__(self) -> typing.Iterator[DBObjTy]:
        """
        Returns the database objects that match the query.
        """

        yield from self.target_def.get_matching(self.where, self.order,
            self.limit, self.offset)

    def count(self) -> int:
        """
        Count the number of objects matching the query.

        Note that len(query) is not supported (call query.count() instead)
        because doing list(query) to store the results of a query would end up
        calling len(query) and needlessly perform two queries.
        """

        return self.target_def.count_matching(self.where, self.order,
            self.limit, self.offset)

    def delete(self) -> None:
        """
        Deletes the database objects that match the query.
        """

        self.target_def.delete_matching(self.where, self.order, self.limit,
            self.offset)


class DBObjectMeta(type):
    """
    Metaclass for DBObject subclasses. Used to property set the __slots__ on
    the subclasses, depending on their defined attributes.
    """

    def __new__(mcs, name: str, bases: tuple[type, ...], class_dict: AnyDict,
        **kwargs: typing.Any) -> 'DBObjectMeta':
        if bases == (DBObjectBase,):
            # This is DBObject being initialized, do nothing special
            assert not kwargs, repr(kwargs)
            return super().__new__(mcs, name, bases, class_dict)

        table = kwargs.pop('table', '').strip()
        kw_only = bool(kwargs.pop('kw_only', False))

        if not table:
            raise TypeError(f"Missing or empty 'table=...' parameter when "
                f"defining class {name}")

        if kwargs:
            key, _ = kwargs.popitem()
            raise TypeError(f"Unknown class parameter {key!r}")

        if DBObject not in bases:
            raise TypeError("DBObject subclasses cannot be subclassed")

        if '__slots__' in class_dict:
            raise ValueError("Do not set __slots__ on DBObject subclasses, "
                "it will be set automatically")

        field_attrs = TableDef.prepare_class_dict(class_dict, kw_only)

        new_cls = super().__new__(mcs, name, bases, class_dict)
        assert issubclass(new_cls, DBObject)
        new_cls._table_def = TableDef.create(new_cls, table, field_attrs,
            kw_only)

        return new_cls


@typing.dataclass_transform(eq_default=False, field_specifiers=(field, unique))
class DBObject(DBObjectBase, metaclass=DBObjectMeta):
    """
    Base class for objects stored in the database.
    """

    # Nominally, SomeClass._table_def = TableDef[SomeClass]
    _table_def: typing.ClassVar[TableDef[typing.Any]]

    __slots__ = ['_db_props']

    def __init__(self: DBObjTy, *args: typing.Any, **kwargs: typing.Any):
        self.__class__._table_def.init_obj(self, args, kwargs)
        self.save()

    @classmethod
    def new_empty(cls) -> typing.Self:
        """
        Creates an empty instance of the object.
        """

        obj = object.__new__(cls)
        cls._table_def.init_empty_obj(obj)

        return obj

    @classmethod
    def get_one(cls, **kwargs: typing.Any) -> typing.Self:
        """
        Finds one object from the table, given the provided search arguments.
        Raises a KeyError if no items matched, ValueError if more than one item
        matched.
        """

        values = iter(cls.get_all(**kwargs))

        try:
            value = next(values)
        except StopIteration:
            raise KeyError(f"{cls.__name__}: No items matching the "
                "search") from None

        try:
            next(values)
        except StopIteration:
            return value

        raise ValueError(f"{cls.__name__}: Multiple items matching the "
            "search") from None

    @classmethod
    def get_opt(cls, **kwargs: typing.Any) -> typing.Optional[typing.Self]:
        """
        Finds one object from the table, given the provided search arguments.
        Returns None if no item was found.
        Raises a ValueError if the table contains more than one item matching
        the search.
        """

        try:
            return cls.get_one(**kwargs)
        except KeyError:
            return None

    @classmethod
    def get_all(cls: type[DBObjTy], **kwargs: typing.Any) -> Query[DBObjTy]:
        """
        Returns a query object that can be used to get or delete the items
        matching the search.
        """

        return Query(cls._table_def, kwargs)

    def save(self) -> None:
        """
        Saves the object to the database.
        """

        self._table_def.save_obj(self)

    def delete(self) -> None:
        """
        Deletes the object from the database. Trying to save the object
        afterwards will recreate it.
        """

        self._table_def.delete_obj(self)

    if not typing.TYPE_CHECKING:
        # Only define these methods when not type checking so the type checker
        # restricts attribute get/set to the annotated types
        def __setattr__(self, key: str, value: typing.Any) -> None:
            self._table_def.handle_obj_assign(self, key, value)
            super().__setattr__(key, value)

        def __getattr__(self, key: str) -> typing.Any:
            value = self._table_def.resolve_fk(self, key)
            if value is None:
                # __getattr__ may have been called because the user called a
                # property, and the property raised an AttributeError. If that
                # is the case, object.__getattribute__ will query it again,
                # bypassing this __getattr__, and the user will hopefully see
                # the error raised by the property. (If the attribute really
                # does not exist, it will raise an AttributeError itself)
                return object.__getattribute__(self, key)

            return value

    def __repr__(self) -> str:
        return self._table_def.get_obj_repr(self)

    def db_has_id(self) -> bool:
        return self._table_def.check_has_id(self)

    @classmethod
    def db_get_table_name(cls) -> str:
        return cls._table_def.name

    class AlreadyExists(Exception):
        """
        Error raised when a UNIQUE constraint fails at creation or update.
        """
