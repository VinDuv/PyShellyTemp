"""
ORM tests
"""

from unittest.mock import call, patch, MagicMock, sentinel
from copy import deepcopy
import datetime
import enum
import typing
import unittest

from pyshellytemp.db import DBObject, reg_db_conv, reg_db_type, field, unique
from pyshellytemp.db.access import DBFKField, DBValueField, DBType, DBQuery
from pyshellytemp.db.access import DBCmpOp, DBOrder, DBUniqueError
from pyshellytemp.db.orm import DBObjectProps, TableDef


class CopyingMock(MagicMock):
    def __call__(self, *args, **kwargs):
        args = deepcopy(args)
        kwargs = deepcopy(kwargs)
        return super(CopyingMock, self).__call__(*args, **kwargs)


def db_query_deepcopy(db_query, memo):
    return DBQuery(table_name=db_query.table_name,
        filter=list(deepcopy(item, memo) for item in db_query.filter),
        order=list(deepcopy(item, memo) for item in db_query.order),
        offset=deepcopy(db_query.offset),
        max_count=deepcopy(db_query.max_count),
    )


@patch.dict('pyshellytemp.db.orm.TableDef.tables')
@patch.dict('pyshellytemp.db.fields.ORMValueField._registered_types')
@patch('pyshellytemp.db.access.DBQuery.__deepcopy__', db_query_deepcopy,
    create=True)
@patch('pyshellytemp.db.orm.database', new_callable=CopyingMock)
class ORMTests(unittest.TestCase):
    def test_usage(self, mock_db):
        @reg_db_conv(datetime.time, str)
        class TimeConverter:
            @staticmethod
            def py_to_db(py_val):
                return py_val.isoformat()

            @staticmethod
            def db_to_py(db_val):
                return datetime.time.fromisoformat(db_val)

        @reg_db_type(int)
        class SomeEnum(enum.Enum):
            VAL1 = 1
            VAL2 = 42

            @staticmethod
            def py_to_db(py_val):
                return py_val.value

            @classmethod
            def db_to_py(cls, db_val):
                return cls(db_val)

        class TestData1(DBObject, table='testdata1'):
            SOME_CONSTANT: typing.ClassVar[int] = 42

            bool_val: bool
            enum_val: SomeEnum = unique()
            str_val: str | None = "Some string"
            float_val: float = field(default=42, is_unique=True)

            def some_func(self):
                return self

        class TestData2(DBObject, table='testdata2', kw_only=True):
            timestamp: datetime.datetime
            ref_nullable: typing.Optional[TestData1] = field(
                default_factory=lambda: None)
            ref_nonnull: TestData1

        TableDef._create_tables(mock_db)
        mock_db.create_table.assert_has_calls([
            call('testdata1', {
                'id': DBValueField(DBType.PKEY, False, False),
                'bool_val': DBValueField(DBType.INT, False, False),
                'enum_val': DBValueField(DBType.INT, True, False),
                'str_val': DBValueField(DBType.STR, False, True),
                'float_val': DBValueField(DBType.FLOAT, True, False),
            }),
            call('testdata2', {
                'id': DBValueField(DBType.PKEY, False, False),
                'timestamp': DBValueField(DBType.FLOAT, False, False),
                'ref_nullable_id': DBFKField('testdata1', 'id', False, True),
                'ref_nonnull_id': DBFKField('testdata1', 'id', False, False),
            }),
        ])

        # Direct creation
        mock_db.insert.return_value = 12345
        td1 = TestData1(True, SomeEnum.VAL2)
        mock_db.insert.assert_called_once_with('testdata1', {
            'bool_val': 1,
            'enum_val': 42,
            'str_val': "Some string",
            'float_val': 42,
        })

        self.assertEqual(td1._db_props, DBObjectProps(12345, {}, {}))

        self._check_obj_props(td1,
            cls=TestData1,
            SOME_CONSTANT=42,
            id=12345,
            bool_val=True,
            enum_val=SomeEnum.VAL2,
            str_val="Some string",
            float_val=42,
        )

        self.assertEqual(repr(td1), "TestData1(id=12345, bool_val=True, "
            "enum_val=<SomeEnum.VAL2: 42>, str_val='Some string', "
            "float_val=42)")

        # Modification
        td1.id = 42
        td1.str_val = "Another string"
        td1.enum_val = SomeEnum.VAL1

        self.assertEqual(td1._db_props, DBObjectProps(12345, {}, {
            'id': 42,
            'str_val': "Another string",
            'enum_val': 1,
        }))

        mock_db.update_equal.assert_not_called()
        td1.save()
        mock_db.update_equal.assert_called_with('testdata1', 'id', 12345, {
            'id': 42,
            'str_val': "Another string",
            'enum_val': 1,
        })
        self.assertEqual(td1._db_props, DBObjectProps(42, {}, {}))

        # Delayed save
        mock_db.reset_mock()
        td2 = TestData2.new_empty()
        self._check_obj_props(td2,
            cls=TestData2,
            ref_nullable=None
        )

        self.assertEqual(repr(td2), "TestData2(id=<missing>, "
            "timestamp=<missing>, ref_nullable=None, ref_nonnull=<missing>)")

        td2.timestamp = datetime.datetime(1970, 1, 1, 1, 0, 42,
            tzinfo=datetime.timezone.utc)
        td2.ref_nonnull = td1

        self.assertEqual(td2._db_props, DBObjectProps(-1, {}, {
            'ref_nullable_id': None,
            'timestamp': 3642.0,
            'ref_nonnull_id': 42,
        }))

        mock_db.insert.assert_not_called()

        td2.save()

        mock_db.insert.assert_called_once_with('testdata2', {
            'ref_nullable_id': None,
            'timestamp': 3642.0,
            'ref_nonnull_id': 42,
        })

        self.assertEqual(td2.id, 12345)

        mock_db.reset_mock()
        td2.ref_nullable = td1
        td2.save()
        mock_db.update_equal.assert_called_with('testdata2', 'id', 12345, {
            'ref_nullable_id': 42,
        })

        mock_db.reset_mock()
        td2.save()
        mock_db.insert.assert_not_called()
        mock_db.update_equal.assert_not_called()

        # Simple fetch
        mock_db.select.return_value = [
            [1, 1, 1, "A", 1.0],
            [2, 0, 42, "B", 3.14],
        ]

        items = list(TestData1.get_all())
        mock_db.select.assert_called_once_with(
            ['id', 'bool_val', 'enum_val', 'str_val', 'float_val'],
            DBQuery(
                table_name='testdata1',
                filter=[],
                order=[],
                offset=-1,
                max_count=-1,
            ),
        )

        self.assertEqual(len(items), 2, repr(items))
        self._check_obj_props(items[0],
            cls=TestData1,
            SOME_CONSTANT=42,
            id=1,
            bool_val=True,
            enum_val=SomeEnum.VAL1,
            str_val="A",
            float_val=1.0,
        )
        self._check_obj_props(items[1],
            cls=TestData1,
            SOME_CONSTANT=42,
            id=2,
            bool_val=False,
            enum_val=SomeEnum.VAL2,
            str_val="B",
            float_val=3.14,
        )

        # Complex fetch
        mock_db.reset_mock()
        mock_db.select.return_value = [
            [1, 1, 1, "A", 1.0],
        ]

        items = list(TestData1.get_all(bool_val=True).filter(enum_val=
            SomeEnum.VAL1, str_val__lt="B", float_val__gte=1.0)[1:10].order_by(
                'str_val', '-float_val'))
        mock_db.select.assert_called_once_with(
            ['id', 'bool_val', 'enum_val', 'str_val', 'float_val'],
            DBQuery(
                table_name='testdata1',
                filter=[
                    ('bool_val', DBCmpOp.EQ, 1),
                    ('enum_val', DBCmpOp.EQ, 1),
                    ('str_val', DBCmpOp.LT, "B"),
                    ('float_val', DBCmpOp.GTE, 1.0),
                ],
                order=[
                    ('str_val', DBOrder.ASC),
                    ('float_val', DBOrder.DESC),
                ],
                max_count=9,
                offset=1,
            ),
        )

        self.assertEqual(len(items), 1, repr(items))
        self._check_obj_props(items[0],
            cls=TestData1,
            SOME_CONSTANT=42,
            id=1,
            bool_val=True,
            enum_val=SomeEnum.VAL1,
            str_val="A",
            float_val=1.0,
        )

        # Complex raw fetch
        mock_db.reset_mock()
        mock_db.select.return_value = sentinel.raw_fetch_res

        data = TestData1.get_all(bool_val=True).filter(enum_val=
            SomeEnum.VAL1, str_val__lt="B", float_val__gte=1.0)[1:10].order_by(
                'str_val', '-float_val').get_raw_fields('id', 'float_val')
        mock_db.select.assert_called_once_with(
            ('id', 'float_val'),
            DBQuery(
                table_name='testdata1',
                filter=[
                    ('bool_val', DBCmpOp.EQ, 1),
                    ('enum_val', DBCmpOp.EQ, 1),
                    ('str_val', DBCmpOp.LT, "B"),
                    ('float_val', DBCmpOp.GTE, 1.0),
                ],
                order=[
                    ('str_val', DBOrder.ASC),
                    ('float_val', DBOrder.DESC),
                ],
                max_count=9,
                offset=1,
            ),
        )

        self.assertIs(data, sentinel.raw_fetch_res)

        # Complex count
        mock_db.reset_mock()
        mock_db.select.return_value = [
            [25],
        ]

        item_count = TestData1.get_all(bool_val=True).filter(enum_val=
            SomeEnum.VAL1, str_val__lt="B", float_val__gte=1.0)[1:10].order_by(
                'str_val', '-float_val').count()
        mock_db.select.assert_called_once_with(
            ('count(*)',),
            DBQuery(
                table_name='testdata1',
                filter=[
                    ('bool_val', DBCmpOp.EQ, 1),
                    ('enum_val', DBCmpOp.EQ, 1),
                    ('str_val', DBCmpOp.LT, "B"),
                    ('float_val', DBCmpOp.GTE, 1.0),
                ],
                order=[
                    ('str_val', DBOrder.ASC),
                    ('float_val', DBOrder.DESC),
                ],
                max_count=9,
                offset=1,
            ),
        )

        self.assertEqual(item_count, 25)

        # Complex fetch with limit of zero (lower > upper)
        mock_db.reset_mock()
        mock_db.select.return_value = []

        items = list(TestData1.get_all(bool_val=True).filter(enum_val=
            SomeEnum.VAL1, str_val__lt="B", float_val__gte=1.0)[6:5].order_by(
                'str_val', '-float_val'))
        mock_db.select.assert_called_once_with(
            ['id', 'bool_val', 'enum_val', 'str_val', 'float_val'],
            DBQuery(
                table_name='testdata1',
                filter=[
                    ('bool_val', DBCmpOp.EQ, 1),
                    ('enum_val', DBCmpOp.EQ, 1),
                    ('str_val', DBCmpOp.LT, "B"),
                    ('float_val', DBCmpOp.GTE, 1.0),
                ],
                order=[
                    ('str_val', DBOrder.ASC),
                    ('float_val', DBOrder.DESC),
                ],
                max_count=0,
                offset=6,
            ),
        )

        self.assertEqual(len(items), 0, repr(items))

        # Zero item return in helper methods
        mock_db.select.return_value = []

        with self.assertRaisesRegex(KeyError, "TestData1: No items matching "
            "the search"):
            TestData1.get_one()

        self.assertIsNone(TestData1.get_opt())

        # Multiple item return in helper methods
        mock_db.select.return_value = [
            [1, 1, 1, "A", 1.0],
            [2, 0, 42, "B", 3.14],
        ]

        with self.assertRaisesRegex(ValueError, "TestData1: Multiple items "
            "matching the search"):
            TestData1.get_one()

        with self.assertRaisesRegex(ValueError, "TestData1: Multiple items "
            "matching the search"):
            TestData1.get_opt()

        # FK delayed fetch
        mock_db.reset_mock()
        mock_db.select.return_value = [
            [12345, 3642, None, 1],
        ]

        item = TestData2.get_one(id=12345)
        mock_db.select.assert_called_once_with(
            ['id', 'timestamp', 'ref_nullable_id', 'ref_nonnull_id'],
            DBQuery(
                table_name='testdata2',
                filter=[('id', DBCmpOp.EQ, 12345)],
                order=[],
                offset=-1,
                max_count=-1,
            ),
        )

        # Date/times from database are currently returned in local naive format
        expected_dt = datetime.datetime(1970, 1, 1, 1, 0, 42,
            tzinfo=datetime.timezone.utc).astimezone(None).replace(tzinfo=None)

        self._check_obj_props(item,
            cls=TestData2,
            id=12345,
            timestamp=expected_dt,
            ref_nullable=None,
        )

        self.assertEqual(repr(item), f"TestData2(id=12345, "
            f"timestamp={expected_dt!r}, ref_nullable=None, "
            f"ref_nonnull=TestData1.get_one(id=1))")

        mock_db.reset_mock()
        self.assertIsNone(item.ref_nullable)
        mock_db.select.assert_not_called()

        mock_db.select.return_value = [
            [1, 1, 1, "A", 1.0],
        ]
        item_ref = item.ref_nonnull

        mock_db.select.assert_called_once_with(
            ['id', 'bool_val', 'enum_val', 'str_val', 'float_val'],
            DBQuery(
                table_name='testdata1',
                filter=[
                    ('id', DBCmpOp.EQ, 1),
                ],
                order=[],
                max_count=-1,
                offset=-1,
            ),
        )

        self._check_obj_props(item_ref,
            cls=TestData1,
            SOME_CONSTANT=42,
            id=1,
            bool_val=True,
            enum_val=SomeEnum.VAL1,
            str_val="A",
            float_val=1.0,
        )

        mock_db.reset_mock()
        self.assertIs(item.ref_nonnull, item_ref)
        mock_db.select.assert_not_called()

        # Single deletion
        mock_db.reset_mock()
        td2.delete()
        mock_db.delete_equal.assert_called_once_with('testdata2', 'id', 12345)
        self.assertEqual(repr(td2), "TestData2(<deleted>)")

        # Complex deletion
        mock_db.reset_mock()
        TestData1.get_all(bool_val=True).filter(enum_val=SomeEnum.VAL1,
            str_val__lt="B", float_val__gte=1.0)[:].order_by('str_val',
                '-float_val').delete()
        mock_db.delete_matching.assert_called_once_with(
            DBQuery(
                table_name='testdata1',
                filter=[
                    ('bool_val', DBCmpOp.EQ, 1),
                    ('enum_val', DBCmpOp.EQ, 1),
                    ('str_val', DBCmpOp.LT, "B"),
                    ('float_val', DBCmpOp.GTE, 1.0),
                ],
                order=[
                    ('str_val', DBOrder.ASC),
                    ('float_val', DBOrder.DESC),
                ],
                max_count=-1,
                offset=0,
            ),
        )

    def test_invalid_defs(self, _mock_db):
        with self.assertRaisesRegex(ValueError, "Cannot use default and "
            "default_factory simulatenously"):
            class FieldTwoDefaults(DBObject, table='whatever'):
                x: int = field(default=1, default_factory=lambda: 42)

        class SomeType:
            pass

        with self.assertRaisesRegex(TypeError, "No database converter "
            "registered for Python type 'SomeType'"):
            class FieldUnregType(DBObject, table='field_unreg'):
                x: SomeType
            f = FieldUnregType(SomeType())

        with self.assertRaisesRegex(ValueError, "A converter is already "
            "registered for Python type 'int'"):
            @reg_db_conv(int, int)
            class IntConverter:
                @staticmethod
                def py_to_db(py_val):
                    return py_val

                @staticmethod
                def db_to_py(db_val):
                    return db_val

        with self.assertRaisesRegex(ValueError, "Reserved name 'id' cannot be "
            "used in DBObject subclasses"):
            class FieldWithReservedName(DBObject, table='whatever'):
                id: int = 42

        class DupName1(DBObject, table='dupname'):
            x: int

        with self.assertRaisesRegex(ValueError, "Table name 'dupname' already "
            "used for type 'DupName1'"):
            class DupName2(DBObject, table='dupname'):
                x: int

        with self.assertRaisesRegex(TypeError, f"non-default argument 'y' "
            "follows default argument"):
            class FieldFollowsDefault(DBObject, table='whatever'):
                x: int = 42
                y: int

        with self.assertRaisesRegex(ValueError, f"DupDBName: Fields 'x_id' and "
            "'x' have the same database field name 'x_id'. Rename one of them "
            "to avoid the conflict."):
            class DupDBName(DBObject, table='whatever'):
                x: DupName1
                x_id: int

            DupDBName(None, 1)

        with self.assertRaisesRegex(ValueError, "The type of field 'test' in "
            "'FieldAsUnion' cannot be a union of types \(only an optional\)"):
            class FieldAsUnion(DBObject, table='field_as_union'):
                test: int | float
            FieldAsUnion()

        with self.assertRaisesRegex(ValueError, "Field 'test' in "
            "'ParametrizedField' cannot have a parametrized type"):
            class ParametrizedField(DBObject, table='parametrized_type'):
                test: typing.ClassVar[int]
            ParametrizedField()

        with self.assertRaisesRegex(TypeError, "Missing or empty 'table=...' "
            "parameter when defining class EmptyTableName"):
            class EmptyTableName(DBObject, table=''):
                x: int


        with self.assertRaisesRegex(TypeError, "Unknown class parameter 'abc'"):
            class BadClassParameter(DBObject, table='test', abc='def'):
                x: int

        with self.assertRaisesRegex(TypeError, "DBObject subclasses cannot be "
            "subclassed"):
            class SubSubclass(DupName1, table='whatever'):
                y: int

        with self.assertRaisesRegex(ValueError, "Do not set __slots__ on "
            "DBObject subclasses, it will be set automatically"):
            class SlotsAlreadyDefined(DBObject, table='test'):
                __slots__ = ['x']
                x: int

    def test_value_checks(self, _mock_db):
        class TestData1(DBObject, table='testdata1', kw_only=True):
            int_val: int = 42
            float_val: float = 3.1415

        class TestData2(DBObject, table='testdata2'):
            td1: TestData1

        with self.assertRaisesRegex(ValueError, "Field 'int_val' is not "
            "nullable; cannot be set to None"):
            TestData1(int_val=None)

        with self.assertRaisesRegex(ValueError, "Field 'float_val' can only "
            "accept values of type int or float"):
            TestData1(float_val="test")

        with self.assertRaisesRegex(ValueError, "Field 'int_val' can only "
            "accept values of type 'int'"):
            TestData1(int_val="test")

        with self.assertRaisesRegex(TypeError, "TestData1 only accepts keyword "
            "arguments"):
            TestData1(123)

        with self.assertRaisesRegex(ValueError, "Field 'td1' can only "
            "accept values of type 'TestData1'"):
            TestData2(td1=42)

        with self.assertRaisesRegex(ValueError, "Field 'td1' can only "
            "accept objects that have been saved to the database"):
            TestData2(td1=TestData1.new_empty())

        with self.assertRaisesRegex(TypeError, "TestData2 got too many "
            "positional arguments \(max 1\)"):
            TestData2(TestData1.new_empty(), TestData1.new_empty())

        with self.assertRaisesRegex(TypeError, "TestData2 got multiple values "
            "for argument 'td1'"):
            TestData2(TestData1.new_empty(), td1=TestData1.new_empty())

        with self.assertRaisesRegex(TypeError, "Missing argument 'td1' to "
            "initialize TestData2 instance; no default value available"):
            TestData2()

        with self.assertRaisesRegex(TypeError, "TestData1 got an unexpected "
            "keyword argument 'invalid'"):
            TestData1(invalid=12345)

        with self.assertRaisesRegex(ValueError, "Cannot set a negative "
            "database ID"):
            TestData1.new_empty().id = -123

        with self.assertRaisesRegex(ValueError, "Unable to save object: No "
            "value set for field 'td1'"):
            TestData2.new_empty().save()

    def test_usage_checks(self, mock_db):
        class TestData1(DBObject, table='testdata1', kw_only=True):
            int_val: int = 42

        mock_db.insert.return_value = 12345
        td1 = TestData1()
        td1.int_val = 42
        with self.assertRaisesRegex(ValueError, "Cannot delete a modified "
            "object"):
            td1.delete()

        with self.assertRaisesRegex(AttributeError, f"'TestData1' object has "
            f"no attribute 'unknown_val'"):
            list(TestData1.get_all(unknown_val="abc"))

        td1 = TestData1()
        td1.delete()
        with self.assertRaisesRegex(ValueError, "This object was deleted from "
            "the database and can no longer be used"):
            td1.int_val = 42

        with self.assertRaisesRegex(ValueError, "This query is already "
            "filtered using 'int_val'"):
            TestData1.get_all(int_val=42).filter(int_val=42)

        with self.assertRaisesRegex(ValueError, "Continuous integer slice "
            "\[start:end\] expected"):
            TestData1.get_all()['a']

        with self.assertRaisesRegex(ValueError, "Query limits already set"):
            TestData1.get_all()[0:10][1:11]

        with self.assertRaisesRegex(TypeError, "'a': integer expected"):
            TestData1.get_all()['a':42]

        with self.assertRaisesRegex(ValueError, "Cannot get the end part of "
            "the query, reverse the ordering and get the begin part instead"):
            TestData1.get_all()[-10:]

        with self.assertRaisesRegex(TypeError, "'a': integer expected"):
            TestData1.get_all()[0:'a']

        with self.assertRaisesRegex(ValueError, "Cannot exclude a specified "
            "number of end items, reverse the ordering and set a start offset "
            "instead"):
            TestData1.get_all()[:-10]

        mock_db.insert.side_effect = DBUniqueError("UNIQUE constraint failed")
        with self.assertRaisesRegex(TestData1.AlreadyExists, "UNIQUE "
            "constraint failed"):
            TestData1()

    def _check_obj_props(self, obj, **expected):
        actual = {
            'cls': type(obj),
        }

        for key in object.__dir__(obj):
            if key.startswith('_'):
                continue

            try:
                attr = object.__getattribute__(obj, key)
            except AttributeError:
                continue

            if callable(attr):
                continue

            actual[key] = attr

        self.assertEqual(actual, expected)



