PyShellyTemp contains its own implementation of a miniature Web framework. This
document explains how it works and how to use it.

**Note**: The framework uses type annotations, but you don’t necessarily need to
use them in your code (except for the database type definitions). It is
hopefully compatible with `from __future__ import annotations` but does not uses
or requires it.

Web routing
===========

Files: `pyshellytemp/web/*` and `pyshellytemp/log_conf.py`.

This handles routing URLs to *view functions*. Here is a basic example:

```python
from pyshellytemp.web import route, HTTPRequest, HTTPTextResponse

@route('/')
def main(request: HTTPRequest) -> HTTPTextResponse:
    return HTTPTextResponse("Hello, world!")

application = route.get_wsgi_app()
```

This defines a WSGI `application` object that WSGI application servers will
accept. Accessing the URL `/` will return `Hello, world!`, accessing any other
page will return a basic 404 page.

Log control
-----------

The framework automatically configures the log level of the Python process to
`INFO`, via the `pyshellytemp.log_conf.configure_logging` function that is
implicitly called at startup. You can set the log level to `DEBUG` globally
by setting the `LOG_DEBUG` environment variable to `1`. You can also set this
variable to a comma-separated list of Python modules; the log level for these
modules will be set to `DEBUG` while the main level stays `INFO`. For instance,
`LOG_DEBUG=db` will set the log level of the `pyshellytemp.db` module to `DEBUG`
(the `pyshellytemp` prefix is implied if no modules match the name without the
prefix). This will cause the application to show the SQL queries performed by
the database module.

Type-safe object associated data
--------------------------------

The Web framework has support for extensions/plugins that can add functionality
to some parts of the code. These extensions may need to attach data to an object
defined by the framework, so the developer can use it.

In Python, this is traditionally done by adding an attribute to the object, but
this has downsides:
- Two extensions might try to add the same attribute name to the object
- Type checkers do not allow it (after all, the role of a type checker is to
  detect typos, and if you allow any attribute assignment you cannot do this)

You can solve the type checker problem by adding a attribute (`ext_data` for
instance) on the object, that is a dictionary that extensions can add their data
into. But there are still a possibility of conflicts, and this prevents type
checkers from doing their job fully as they cannot known the type of
`some_object.ext_data['my_extension']`.

To solve this problem, instead of identifying the extension data by a string,
you can identify it by a type! The way it works is that the extension code
defines a private class, say `MyExtensionData`. When it wants to attach data
to an object, it does:
```
some_object.set_ext_data(MyExtensionData(…))
```

And when some other part of the code wants to access the extension data, it
does:
```
some_object.get_ext_data(MyExtensionData)
```

This returns the `MyExtensionData(…)` object that was set by the extension. This
has two advantages:
- There is no possible clash between two extensions; even if they both define a
  type with the same name, they are still two distinct types, and they will be
  stored separately.
- The type checker knows that `some_object.get_ext_data(SomeClass)` returns a
  `SomeClass` object, so it can properly type-check the code that use the return
  value.

The main disadvantage is that it looks more complicated than a simple attribute
access.

This method is used in the framework to add data to requests and views (see
below for details).

Response types
--------------

Multiple type of response classes are available; you can see their definition
in `pyshellytemp/web/response.py` or define you own. It needs to provide the
methods defined in the `HTTPResponse` protocol.

- `HTTPTextResponse`: returns the provided text, encoded as UTF-8, in the
  response. The content-type and HTTP status of the response can be customized.
  The `HTTPTextResponse.msg_page` class method formats a title and a message
  into a basic HTML page; this can be used for basic error pages.
- `HTTPFileResponse`: used to return the contents of a file. The
  `HTTPFileResponse.serve_file` class method can be used to implement static
  file serving.
- `HTTPError` is an exception designed to be raised from view functions. It will
  generate an HTML error page will the provided information that will be shown
  to the user. This can be useful for utility functions that want to interrupt
  the view’s processing an return an error to the user. You can write your own
  exceptions that behave like this by subclassing `HTTPBaseError`.

Request object
--------------

The `request` object provided to the view function when called provides
information on the request: URL path, headers, query string (as a dictionary).
If the request was done using POST, the `post` attribute is not None and
contains a file object that can be used to get the POST data. It can also
directly parse POST form data into a dictionary via the `get_form_data` method;
note that it only supports `application/x-www-form-urlencoded` forms.

See `pyshellytemp/web/request.py` for the full definition.

Routing and views
-----------------

Using the `@route('/some/path')` on a function indicates to the framework that
this function will be called when a requests targets the indicated path. Note
that the framework supports being mounted somewhere other than on `/` and will
adjust all paths accordingly (assuming the WSGI server correctly handles
`SCRIPT_NAME` and `PATH_INFO`).

The path put in the route definition can contains *placeholders* in the form
`{var_name}`; they will capture parts of the URL and put them into variables
passed to the view.
- A placeholder in the middle of the URL will match any non-/ characters
  (1 character minimum)
- A placeholder at the very end of the URL will match any characters
  (including zero characters)
- A placeholder suffixed with `:d` will match a positive integer; the
  extracted result will be provided as an int instead of a string.

Here are a few examples (note that you can stack `@route(…)` on a single
function):
```python
@route('/user/{user_id:d}')
@route('/user/new')
def user_edit(request: HTTPRequest, user_id: int | None = None) -> HTTPResponse:
    # Will match /user/1234 (calls user_edit with user_id=1234 (int))
    # Will not match /user/-1, /user/, /user/abcd
    # Will match /user/new specifically (calls user_edit without user_id so
    # the default value of None is used)
    
@route('/article/{name}/')
def article(request: HTTPRequest, name: str) -> HTTPResponse:
    # Will match /article/something@$*!/ (calls article with
    # name='something@$*!')
    # Will not match /article/something/else/ (slash characters are not matched)

@route('/static/{path})
def static(request: HTTPRequest, path: str) -> HTTPResponse:
    # Will match /static/, /static/abcdef, /static/some/complex/p@th! and put
    # the corresponding value ('', 'abcdef', 'some/complex/p@th!') in the path
    # variable. This is handy in conjuction with HTTPFileResponse.serve_file to
    # serve static data.
```

The routing does end slash autocorrection: If no view is matched by an URL that
ends with something other than a slash, it tries to see if there is a match
after adding a slash. If that’s the case, it returns a permanent redirect to the
URL with the slash (without calling the target view; it will be called after
the redirect)

The decorator returns a `View` instance (defined in
`pyshellytemp/web/routing.py`); in the above example, `user_edit`, `article` and
`static` are `View`s, which have a few methods, mostly used internally.
The `get_path` method returns a path, with the placeholders replaced with the
provided in the dictionary argument. For instance, `user_edit.get_path({})` will
return `'/user/new'` and `user_edit.get_path({'user_id': 42})` will return
`'/user/42'`.
Note that these are application-internal paths; to form a complete
URL, use `pyshellytemp.web.view_path`, passing a request, a view, and the
placeholder arguments as keywords (for instance
`view_path(request, user_edit, user_id=42)`). You can also use
`redirect_to_view` that directly generates a redirection response to the target
page.

Request context
---------------

Calling `request.get_context()` on a request returns a dictionary of values that
are recovered from the request itself and from any request extensions (see the
next section). The values in this dictionary are useful when using the template
manager to render an HTML page. You probably do not need to interact with this
dictionary directly; instead use the `pyshellytemp.util.render` utility
function described later in this documentation, to render a template as HTML
with the request context.

Request extensions (advanced feature)
-------------------------------------

Request extensions are a mechanism used to add information to received requests
when they are received, before them being sent to a view function. They are
similar to the *middlewares* used by Django.

To define a request extension, add the `@route.request_extension` decorator on
a function. It should take the following parameters:
- `request`: the `HTTPRequest` that was received
- `view`: the `View` that was identified as the target of the request.
- `extra_headers`: a dictionary of headers that will be added to the response
  returned by the view before being returned to the client (can be used to set
  cookies, for instance)

It should either return `None` to continue processing though the `View`, or
return an `HTTPResponse`; if that happens the response is returned to the client
and the view function is not invoked.

You may want to to add a marker on views that need to get special behavior from
your extension. To to this, you can define a decorator that will add associated
data to the target `View`, via the `view.set_prop` method. For instance:

```python

# Indicate that views should only be accessible on Monday

# Define associated data class that will be set on the target views
@dataclasses.dataclass
class OnlyOnMonday:
    enabled: bool = False

# Decorator that sets it
def only_on_monday(view: View) -> View:
    view.set_prop(OnlyOnMonday(True))

# Usage on the view
@only_on_monday
@route('/some/path')
def some_view(…):

# Usage in the request extension function
@route.request_extension
def monday_req_ext(request: HTTPRequest, view: View,
    extra_headers: dict[str, str]) -> HTTPResponse | None:
    
    # get_prop returns its second argument (which has enabled=False) if the
    # associated data is not set on the view
    enabled = view.get_prop(OnlyOnMonday, OnlyOnMonday()).enabled
    
    if enabled:
        # The only_on_monday decorator was set on the view, do whatever
```

Request extension functions can also add associated data on the request itself,
via the `request.set_ext(<data object>)` method. The view function can then
access this data with `request.get_ext(<data class>)` (which will raise a
`KeyError` if the data is not present).

The class used for the associated data must derive from the `ReqExtData` class.
It can optionally override the `put_into_context` method that is used to add
values to the template renderer’s context. This is described in more details
later in this documentation.

The `session` extension, provided in `pyshellytemp/session.py` file, makes use
all the features described here.

Database access and ORM
=======================

Files: `pyshellytemp/db/*.py`

The framework only support **SQLite** databases. One database connection is used
for all threads of the application, so the SQLite library must be using the
*serialized* threading mode (this is checked when the database is opened).

Database configuration and low-level access is done through the
*pyshellytemp.db.database* singleton object.

Database path
-------------

The database path is set on the `database` singleton object; this needs to be
done before the database is accessed.

- `database.set_default_db_path(<path>)` sets the default database path; this
  path will be used unless it is overriden by the `set_db_path` method or the
  `DB_PATH` environment variable.
- `database.set_db_path(<path>)` sets the database path to the specified value.

If the database is used with neither of these methods being called, the value
of the `DB_PATH` environment variable will be used; if it is unset or blank,
an error will be raised.

Low-level database access
-------------------------

Raw database queries can be made using the `database` singleton object.

The `exec_raw` method can be used to perform modifications on the database
(create table, insert, update, delete, …). If an insert or update is performed,
the ID of the inserted/modified row is returned, if any.

The `fetch_raw` method can be used to fetch data from the database. It returns
a cursor; iterating over the cursor returns each row returned by the query.

Both methods take a SQL statement as a string, and a list of values that are
inserted into the SQL statement’s placeholders (usually `?`). For instance:

```
ins_id = database.exec_raw('insert into some_table (val1, val2) values (?, ?)', 
    [val1, val2]])
```

Inserting values into placeholders like this avoids the risk of SQL injections.

The `database` object also provides methods that build a SQL statement and
execute it: `create_table`, `select`, `insert`, `update_equal`, `delete_equal`,
`delete_matching`. These are defined mainly for the usage of the ORM, but can
be called directly if needed. They are defined in `pyshellytemp/db/access.py`.

Database initialization
-----------------------

To initialize the database, call the `database.init` method. This will create
the database and any tables defined by the ORM (see below). If the database file
already exists, this will fails, unless the `force=True` parameter is specified.

If your code needs to perform any operations when the database is created, you
can use `database.register_init_hook` to register a database initialization
hook:

```python
@database.register_init_hook(priority=1)
def my_hook(db: Database):
    # Perform operations on 'db' (equivalent to 'database')
```

Database initialization hooks are executed in increasing order of priority
(first priority 0, then 1, etc). Priority zero should be reserved to hooks that
create database tables, so priority 1 hooks can operate on the created tables.
If your hook uses database ORM objects, it should be priority 1 or higher.

ORM definitions
---------------

A database-backed object class is declared as a subclass of the `DBObject` class
(declared in `pyshellytemp.db`). Each field is declared with a type annotation,
`<ident>: <type> [= <default value/field>]`. For instance:

```python
from pyshellytemp.db import DBObject

class SampleObj(DBObject, table='samples'):
    val1: int
    val2: float
    val3: str = "Some value"
```

The `table` class definition parameter indicates the database table that will
store the object. The table will be created automatically by `database.init`.

The field names need to be lowercase and not starting with an underscore; other
names in the class will be considered class globals and ignored.

Database objects have an implicit `id` field of type `int` that stores the
unique ID of the object attributed by the database.

The default values are used if you create a new object and do not set the value
explicitly (they are not used at the database level). All fields with default
values need to be grouped at the end of the definition, unless `kw_only=True`
is specified in the class definition (after `table='xxx'`).

Instead of a default value, you can specify a `pyshellytemp.db.field` object
that set properties on the field after the equals sign. This works the same
way as the Python dataclass `field`:

```python
from pyshellytemp.db import DBObject, field, unique

class AnotherSample(DBObject, table='another'):
    a: int = field(default=42)  # Equivalent to = 42 (so not very useful)
    
    # Default value of b will be set set to the return value of time.time()
    # upon creation of the object
    b: float = field(default_factory=time.time)
    
    # AnotherSample objects stored in the database will all have distinct values
    # for c (marks the database column as UNIQUE)
    c: str = field(is_unique=True)
    
    # Same as field(is_unique=True)
    d: str = unique()
```

### Nullability

By default, the database fields do not accept `None` values (`NULL` at database
level). To be able to store `None` in a field, specify `<type> | None` as its
type (or `typing.Optional[<type>]`):

```python
class AnotherSample(DBObject, table='another'):
    not_null: int = 3
    nullable: int | None = 42
    also_nullable: typing.Optional[int] = None
```

### Stored types

Fields can be of the following types: `int`, `float`, `bool`, `str`, `bytes`,
and `datetime.datetime`.

If you want to use other types for your fields, you can defines converters that
will convert between those types and database-native types (`int`, `float`,
`str`, or `bytes`).

A converter is a class with two static (or class) methods. To convert a Python
value to the corresponding database value, the framework will call
`ConverterClass.py_to_db(<value>)`; to do the opposite conversion, it will call
`ConverterClass.db_to_py(<value>)`.

If you want to define a converter for an existing type, use the `@reg_db_conv`
decorator on your converter class, providing it the Python type and the database
type as parameters. For instance:

```python
# Allows storing `datetime.time` objects in the database, as strings.

@reg_db_conv(datetime.time, str)
class TimeConverter:
    @staticmethod
    def py_to_db(py_val: datetime.time) -> str:
        return py_val.isoformat()

    @staticmethod
    def db_to_py(db_val: str) -> datetime.time:
        return datetime.time.fromisoformat(db_val)
```

If you define a type yourself and want to store it in the database, use the
`@reg_db_type` decorator on the class that defines your type, providing it the
database type as parameter. For instance:

```python
# Allows storing values of SomeEnum in the database, as integers.

@reg_db_type(int)
class SomeEnum(enum.Enum):
    VAL1 = 1
    VAL2 = 42

    @staticmethod
    def py_to_db(py_val: 'SomeEnum') -> int:
        return py_val.value

    @classmethod
    def db_to_py(cls, db_val: int) -> 'SomeEnum':
        return cls(db_val)
```

### Reference to other database objects

A field in an object can reference another database objects (a “foreign key”
in database-speak).

```python
class Sample1(DBObject, table='sample1'):
    value: int

class Sample2(DBObject, table='sample2'):
    sample1: Sample1

class Sample3(DBObject, table='sample3'):
    sample1: Sample1 | None
```

These fields work like normal fields, with the following remarks:
- In order to assign an object to the field, the assigned object must already
  exist in the database (i.e. it needs to be saved, see below).
- When an object is loaded from the database, its reference are resolved lazily,
  on first use; for instance, if you have a `Sample2` object, the referenced
  `Sample1` object will not be loaded from the database until you access
  `sample2.sample1`. Once it is loaded, it will not be reloaded.
- References are kept consistent regarding deletions: non-null references
  propagates the deletion, nullable references are set to None. For instance,
  if a `Sample2` object in the database refers to a `Sample1` object and that
  object is deleted, then the `Sample2` object will be deleted, too. By
  contrast, with `Sample3`, the `sample1` field will be set to `None` in that
  situation.

### Declaration checking

The framework will check that the field types are either built-in types, types
with registered converter, or references to other database objects, but that
check is only performed on first use of the class (object creation, query,
or database initialization). This is because when the class is defined, it can
refer to types that have not be fully initialized yet or whose converter have
not yet be registered.

Create and modify database objects
----------------------------------

To create a new instance of a database object *and save it in the database*,
construct it like a normal object.

```python
class Sample(DBObject, table='samples'):
    int_val: int
    str_val: str = "Hello, world"

# Construct the object with positional syntax and store it in the database
obj = Sample(42, "Test")

# Construct the object with keyword syntax and store it in the database
obj = Sample(int_val=42, str_val="Test")
```

Note that if `kw_only=True` is specified in the class definition, only the
keyword syntax is allowed.

Once you have an object, you can access and modify its fields; the changes will
only be saved to the database when you call `obj.save()`.

If you want to create an object but not save it immediately to the database,
use the `new_empty` class method. It will create an empty object and you can
set its values manually before saving it.

```python
obj = Sample.create_empty()
obj.int_val = 123
# Setting obj.str_val is not required since it has a default value
obj.save()  # Object is created in the database here
```

If you need to explicitly set the `id` of the object being created (dubious but
sometime useful), use the `create_empty` method. If you want to change the `id`
of an already existing object (even more dubious), you can write a new value to
`id` and save the object.

Type checkers will properly check the types of constructed objects and attribute
assignments. They will also check that all non-default attributes are set for
an object created with the constructor syntax. They cannot, however, ensure that
all attributes were set when using the `create_empty` method; if you forget to
set a value, a runtime error will be raised when saving the object.

If one field of the object was marked as `unique()` and you try to create or
save an object with a duplicate of that field, a `<object class>.AlreadyExists`
exception will be raised.

Fetching database objects
-------------------------

Object querying functions take keyword arguments that filter the objects being
returned by the query. Those keyword arguments are formed by the filtered field
name, a double underscore, and a predicate (equal, greater than, less than):
```python
value__eq=1     # Returns objects whose field 'value' == 1
value=1         # Alternate syntax (only for equality)
value__lt=40    # Returns objects with value < 40
value__lte=40   # Returns objects with value <= 40
value__gt=40    # Returns objects with value > 40
value__gte=40   # Returns objects with value >= 40
```

Note that the comparisons are made against the values stored in the database; if
you store a custom type in the database, you should make sure the database
values compare similarly to their Python values.

If you want to query a single object from the database, call the `get_one`
method on the object’s class, giving it filter keywords values:

```python
obj = Sample.get_one(id=42)  # Returns the Sample object whose id is 42
```

This raises a `KeyError` if no object was found and `ValueError` if multiple
objects were found. You can also use `get_opt` that works similarly but returns
`None` if the object was not found.

To retrieve multiple objects, use the `get_all` method:

```python
query = Sample.get_all(int_val__lte=43, str_val="Whatever")
```

This does not directly queries the database; instead, it returns a `Query`
object that supports the following operations (that return a new `Query`):
- Further filtering with the `.where(<filter keywords>)`
- Ordering with the `.order_by('field_name1', 'field_name2', …)` function. Field
  names can be prefixed with `+` for ascending order (the default) and `-`
  for descending order.
- Only get a chunk of the results with the `[<start>:<end>]` operation.

Once a query is built, you can:
- Get all items matching the query by iterating over it
- Count the items matching the query by calling `.count()` on it
- Delete all items matching the query by calling `.delete()` on it

Examples:
```python

for obj in Sample.get_all():
    # Iterates over all objects

# Gets the count of all objects whose int_val is <= 43
Sample.get_all(int_val_lte=43).count()

# - Takes all objects
# - Orders them by decreasing ID
# - Skips over the first 10 results
# - Deletes the rest
# This basically deletes old objects, keeping the 10 newest ones (assuming IDs
# are given sequentially, which they are)
Sample.get_all().order_by('-id')[10:].delete()
```

Template manager
================

Files: `pyshellytemp/tpl_mgr/*.py`

The template manager loads template files and renders them using a dictionary
of variables (called a context).

Files location
--------------

Template files are normally put in the `pyshellytemp/templates` directory. The
template manager also handles an optional “template override directory”, defined
by the `TPL_OVERRIDE_DIR` environment variable. This is to allow the use of the
application to easily modify parts of the templates.

When a template is referred by relative path, the template manager searches for
it in the template override directory (if set), and if not found, in the regular
template directory. There is one exception to this rule in the case of extended
templates; see below.

Rendering
---------

To render a template, it must be first be compiled. To do this, get the template
manager singleton `pyshellytemp.tpl_manager.templates` and call its `get` class
method, providing it the relative path to a template. If the template is found
and its syntax is valid, the method returns a `Template` object. This template
object can be rendered by calling its `render` method, which takes a context
dictionary and returns a string with the result of the render.

Compiling a template may fail due to syntax errors, which are raised as
`templates.get` is called. By contrast, `template.render` never raises an
exception; errors during expression evaluation (see below) result in a blank
string. To debug these errors, set the `TPL_DEBUG` environment variable to `y`;
it will cause the rendering errors to be logged (as a traceback).

Note that when rendering HTML to be returned to the user, it is more convenient
to use the `render` utility function described in the next section; this
function also adds additional context variables that are often needed. Direct
use of `templates.get().render()` should be reserved to special cases (like
rendering an email body).

The `templates.get` method caches the resulting template so it can be called
each time a template needs to be rendered.

Template syntax
---------------

The template syntax matches closely the one used by Django. The following tags
are defined:

```django
Regular text is put into the rendered output without modification (including
HTML tags)
{{ expr }} outputs the value of the expression 'expr' in the result. Depending
on the type of the expression, HTML-special characters (&, <, >) will be
replaced with their associated HTML entities (&amp;, &lt;, &gt;). See below
for details.

{% if expr %}
This is rendered if expr is true
{% else %}
(optional) This is rendered if expr is false
{% endif %}

{% for var in expr %}
The result of expr is iterated over and this is rendered at each iteration; each
time the 'var' variable contains the value returned by the iterator.

You can also specify multiple variables (for var1, var2 in expr) and they will
be bound to the corresponding values of the tuple returned by the iterator, like
in regular Python.
{% else %}
(optional) This is rendered if the iterator was empty (warning: this is
different from regular Python loops)
{% endfor %}

{% block 'some_block_name' %}
Blocks render their content unconditionally; they are used for template
extension, described later.
{% endblock %}
```

When when the result of an expression needs to be put in the output
(`{{ expr }}`), the template manager first checks if the result has a `to_html`
method. If that is the case, the method is called and its output is put
directly in the template. If that’s not the case, the result is converted to
a string by calling `str()` on it, and the result is HTML-escaped before being
put in the template.

If you have an object that generates HTML and don’t want that HTML to be escaped
when rendered, either have the object have a `to_html` function that returns
that HTML, or put its output in a `SafeString` (defined in
`pyshellytemp.tpl_mgr`) and pass that to the template.

Template expressions
--------------------

The expressions you can put in `{{ }}`, `{% if %}`, … need to follow the syntax
of Python expression, but there are additional restrictions. You can perform
the following operations:
- Constants: numbers like `3`, strings like `"a string"` or `'a string'`
- Context access: `x` will get the `x` key from the context dictionary provided
  to the `render` method.
- Attribute access: `expr.x` will get attribute `x` from `expr`
- Indexed/keyed access: `expr1[expr2]` will get key `expr2` from `expr1`
- Parentheses: `(expr)`
- Unary operations: `+expr, -expr, not expr`
- Binary operations: `e1 + e2`, `e1 - e2`, `e1 * e2`, `e1 / e2`, `e1 // e2`,
  `e1 ** e2`
- Comparisons: `e1 == e2`, `e1 > e2`, `e1 >= e2`, `e1 < e2`, `e1 <= e2`,
  `e1 is e2`, `e1 is not e2`, `e1 in e2`, `e1 not in e2`. Note that chained
  comparisons (`a < b < c`) are not supported.

Note that the syntax does not allow function calls. This reduces (but probably
not eliminates) the potential of arbitrary code execution from a template.

Formatters are currently not supported; if an object provides a value and you
want it formatted a certain way in the template, you will need to add a property
on the object that returns a string with the right format (or pass the formatted
value to the template directly).

Template blocks, extensions and overrides
-----------------------------------------

The named blocks `{% block 'name' %}{% endblock %}` allows reusing parts of a
template in another template via the `{% extend %}` syntax.
For instance, if you have a `main.html` template with the following content:
```django
<!DOCTYPE html>
<html lang="en">
<head>
<title>{% block 'title' %}Default title{% endblock %}</title>
</head>
<body>
{% block 'body' %}{% endblock %}
</body>
</html>
```

And a `page.html` template containing:
```django
{% extend 'main.html' %}
{% block 'body' %}Hello, world!{% endblock %}
```

When `page.html` is compiled, the template manager will compile `main.html`,
then replace its `body` block with the block value specified in `page.html`.

`{% extend '…' %}` needs to be at the top of the extending template, and this
template can only contain `{% block '…' %}` at the top level.

Recursive template extension is supported.

Templates placed in the template override directory can extend the original
template. For instance, the override directory can contain a `page.html`
template containing:
```django
{% extend 'page.html' %}
{% block 'title' %}Overriden title{% endblock %}
```

Note that the template is extending itself; in that situation, the template
will extend the `page.html`template from the “normal” template directory (which
will itself extend the `main.html` template).

This is only the case for templates loaded from the template override directory.
Normal templates should not attempt to extend themselves!

Utilities
=========

File: `pyshellytemp/util.py`

This file contain some utilities functions. The `render` function is critical
for HTML template rendering. The other ones are only useful in some situations.

Template-to-view rendering
--------------------------

The `pyshellytemp.utils.render` is a bridge between the URL router and the
template manager; it allows views to easily render HTML from a template and
return it as a HTTP response.

It takes the following parameters:
- The request that was received by the view
- The relative path to the template to render
- The context dictionary to use to render the template (optional)
- The HTTP status code to return, as a http.HTTPStatus enum value (optional)
- A dictionary of headers to add to the response (optional)

The `render` functions makes use of the `request.get_context()` method to add
entries to the context dictionary. These entries are:

- `urlprefix`: The path where the application is mounted on the WSGI server.
  When defining an internal link in the template, always put `{{ urlprefix }}`
  in front of it so the links will work even if the application is mounted
  somewhere other than `/`.
- `urlpath`: the application-internal path of the current page. This can be
  useful if you want to put the path to the current page into a query parameter
  of a link so the target page can redirect back to the initial page.

Request extensions may also add entries to the context; this means that when
`render` is used, those values will be available to the template.

Other utility functions
-----------------------

`join_lines` takes a list of strings, HTML-escapes all of them, then join them,
separated by HTML line breaks. The result is put in a `SafeString` so it will
not be escaped again by the template.
This is useful to concatenate multiple error messages into a multi-line message
without having to worry about HTML-escaping each message.

`float_or_default` takes a string value and a float value. If the string is
a valid float, it is returned. If this is not the case, the provided float value
is returned. This is mainly useful to validate float values from forms.

Session extension
=================

File: `pyshellytemp/session.py`

This file implements basic user and session management. It uses the request
extension mechanism of the Web framework, so if it’s not used at all it has
no effect. Importing the file is sufficient to enable its database models and
views (although you will probably make use of some of its decorators anyway).

Database models
---------------

Two database objects are declared in the `pyshellytemp.session` module: `User`
(a basic user with a username and password) and `Session` (represents an active
user session).

Login/logout views
------------------

These views are placed at `/login` and `/logout` respectively  and uses the
`session/login.html` and `session/logout.html` templates.

The login view supports a `next` query parameter to redirect back to the
original page after login completes. The easiest way to use it is to use
`{{ urlpath }}` in the login link:

```django
<a href="{{ urlprefix }}/login?next={{ urlpath }}">Login</a>
```

The logout view always redirects to the main page of the site after logout.

Decorators and session information
----------------------------------

The `pyshellytemp.session` module defines two view decorators:

- `login_required` can be applied to any view that requires login; if the user
  is not logged in when accessing the view, they will be redirected to the
  login page.
  
- `no_session` can be applied to pages that makes no use of the session
  mechanism; by default the session extension will validate the session on each
  accessed views, and potentially add Set-Cookie headers to refresh the session
  cookie. Using this decorator on a view disables the behavior.

On views where `no_session` was *not* used, the session object can be retrieved
with `request.get_ext(SessionData).session`. If this is not `None`, the user
is currently logged in and can be retrieved with by accessing `.user` on the
session object.

As a shortcut, `login_required` views can retrieve the logged in user by using
`User.from_request(request)`.

Session messages
----------------

It is sometimes required to pass a message between two page loads (for instance,
when redirecting after a successful form POST).
To do this, run `request.get_ext(SessionData).set_next_message('message')` to
set a message (requires the user to be logged in!).
The message can be recovered at the next page load with
`request.get_ext(SessionData).message`. This will return an empty string if
there was no message or the user is not logged in. Note that the message
will be erased at the next page load even if it’s not recovered.
