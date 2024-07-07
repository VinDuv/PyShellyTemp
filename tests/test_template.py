"""
Template manager tests
"""

from unittest.mock import MagicMock, call, patch, sentinel
import linecache
import pathlib
import tempfile
import textwrap
import unittest

from pyshellytemp.tpl_mgr import TemplateManager, ExpressionEvaluator
from pyshellytemp.tpl_mgr import SafeString, Token

TPE = Token.ParseError

class SomeClass:
    def __init__(self):
        self.x = 42

class TestTemplate(unittest.TestCase):
    _temp_dir1 = None
    _temp_dir2 = None

    def test_empty_template(self):
        self.assertEqual(self._render(''), "")

    def test_string_interpolation(self):
        self.assertEqual(self._render('{{ a }}', {}), "")

        context = {'a': '<'}
        self.assertEqual(self._render('{{ a }}', context), "&lt;")

        context = {'a': SafeString('<')}
        self.assertEqual(self._render('{{ a }}', context), "<")

    def test_bad_paths(self):
        # Path is a file
        file_path = pathlib.Path(self._temp_dir1.name) / 'file'
        file_path.write_text('')

        env = {
            'TPL_OVERRIDE_DIR': str(file_path)
        }

        try:
            with self.assertRaisesRegex(SystemExit,
                r"is not accessible or not a directory"):
                with patch('pyshellytemp.tpl_mgr.os.environ', env):
                    TemplateManager.create()
        finally:
            file_path.unlink()

        # Non-existent template
        with self.assertRaises(FileNotFoundError):
            path = pathlib.Path(self._temp_dir1.name)
            TemplateManager(path).get('missing.html')

        # Extend a non-existent template
        with self.assertRaises(FileNotFoundError):
            self._render('{% extend "missing.html" %}')

    def test_template_cache(self):
        base_path = pathlib.Path(self._temp_dir1.name)
        mgr = TemplateManager(base_path)

        file_path = base_path / 'template.txt'
        file_path.write_text('test')
        tpl1 = mgr.get('template.txt')
        tpl2 = mgr.get('template.txt')

        self.assertIs(tpl1, tpl2)

    def test_template_cache_lock(self):
        m = MagicMock()
        m.cache.get.side_effect = [None, sentinel.cached_tpl]
        tpl = TemplateManager(NotImplemented, NotImplemented, m.cache, m.lock)
        result = tpl.get('some_template.html')

        self.assertIs(result, sentinel.cached_tpl)
        m.assert_has_calls([
            # Try to get the template from the cache; returns None
            call.cache.get('some_template.html'),
            # Enter the lock
            call.lock.__enter__(),
            # Try to get the template again; succeeds and return the sentinel
            call.cache.get('some_template.html'),
            # Exit the lock
            call.lock.__exit__(None, None, None),
        ])

    def test_if(self):
        tpl1 = ' {% if a %}\nx\n{% else %}\ny\n{% endif %}'
        tpl2 = ' {% if a %}\nx\n{% endif %}'
        self.assertEqual(self._render(tpl1, {'a': True}), ' x\n')
        self.assertEqual(self._render(tpl2, {'a': True}), ' x\n')
        self.assertEqual(self._render(tpl1, {'a': False}), ' y\n')
        self.assertEqual(self._render(tpl2, {'a': False}), ' ')

    def test_for(self):
        tpl = '{% for x in a %}{{ x }} {% else %}no{% endfor %}'

        self.assertEqual(self._render(tpl, {'a': "abc"}), 'a b c ')
        self.assertEqual(self._render(tpl, {'a': ""}), 'no')
        self.assertEqual(self._render(tpl, {'a': False}), 'no')

        tpl = '{% for x, y in a %}{{ x }} {{ y }} {% else %}no{% endfor %}'
        self.assertEqual(self._render(tpl, {
            'a': ('a1', 'b2', 'c3')
        }), 'a 1 b 2 c 3 ')

        self.assertEqual(self._render(tpl, {}), 'no')

        tpl = '{% for x, y, in a %}{{ x }} {{ y }} {% endfor %}'
        self.assertEqual(self._render(tpl, {
            'a': ('a1', 'b2', 'c3')
        }), 'a 1 b 2 c 3 ')
        self.assertEqual(self._render(tpl, {}), '')

    def test_extend(self):
        std_path = pathlib.Path(self._temp_dir1.name)
        override_path = pathlib.Path(self._temp_dir2.name)

        base_tpl_path = std_path / 'base.html'
        over_tpl_path = override_path / 'base.html'
        ext_tpl_path = std_path / 'extend.html'

        try:
            base_tpl_path.write_text(textwrap.dedent("""
                {% block "outer1" %}a
                {% block "inner1" %}b
                {% endblock %}{% endblock %}
                {% block "outer2" %}c
                {% block "inner2" %}d
                {% endblock %}{% endblock %}
            """).strip())

            over_tpl_path.write_text(textwrap.dedent("""

                {% extend "base.html" %}
                {% block "outer1" %}x
                {% block "inner3" %}y
                {% endblock %}{% endblock %}
            """).strip())

            ext_tpl_path.write_text(textwrap.dedent("""
                {% extend "base.html" %}
                {% block "inner3" %}z
                {% endblock %}
                {% block "outer2" %}A{% endblock %}
            """).strip())

            mgr = TemplateManager(std_path, override_path)
            with patch('pyshellytemp.tpl_mgr.templates', mgr):
                self.assertEqual(mgr.get('extend.html').render({}),
                    "x\nz\n\nA")

        finally:
            base_tpl_path.unlink(missing_ok=True)
            over_tpl_path.unlink(missing_ok=True)
            ext_tpl_path.unlink(missing_ok=True)

    def test_expr_evaluator(self):
        with self.assertRaisesRegex(TPE, r"invalid syntax"):
            self._render('{{ a + }}')

        with self.assertRaisesRegex(TPE, r"Comparison operations are limited "
            "to two operands"):
            self._render('{{ a < b < c }}')

        with self.assertRaisesRegex(TPE, r"unterminated string literal"):
            self._render('{% block " %}')

        with self.assertRaisesRegex(TPE, r"Quoted string expected"):
            self._render('{% block 42 %}')

        # Not sure if the template render regex allows the creation of an
        # invalid operation, currently.
        with self.assertRaisesRegex(SyntaxError, r"Invalid operation 'call'"):
            ExpressionEvaluator('f()')

        self.assertEqual(self._render('{{ -a }}', {'a': 42}), '-42')
        self.assertEqual(self._render('{{ a + 0 }}', {'a': 42}), '42')
        self.assertEqual(self._render('{{ a[0] }}', {'a': [42]}), '42')
        self.assertEqual(self._render('{{ a.x }}', {'a': SomeClass()}), '42')

        self.assertEqual(self._render('{{ a < b }}', {'a': 1, 'b': 2}), 'True')

        with patch('pyshellytemp.tpl_mgr.expr_eval.TPL_DEBUG', True):
            with self.assertLogs() as captured:
                self._render('{{ 1 / 0 }}', {})
            self.assertIn('Error evaluating 1 / 0 in context {}',
                captured.output[0])

    def test_parse_errors(self):
        # Generic token parsing errors
        with self.assertRaisesRegex(TPE, r"Unknown tag 'blah'"):
            self._render('{% blah %}')

        with self.assertRaisesRegex(TPE, r"Unexpected close tag"):
            self._render('{% endfor %}')

        # If block errors
        with self.assertRaisesRegex(TPE, r"Expected endif"):
            self._render('{% if blah %}{% endfor %}')

        with self.assertRaisesRegex(TPE, r"Did not find endif for this if"):
            self._render('{% if blah %}')

        # For block errors
        with self.assertRaisesRegex(TPE, r"Expected endfor"):
            self._render('{% for x in a %}{% endif %}')

        with self.assertRaisesRegex(TPE, r"Did not find endfor for this for"):
            self._render('{% for x in a %}')

        with self.assertRaisesRegex(TPE, r"Expected: var\[, var...\] in value"):
            self._render('{% for abc %}')

        with self.assertRaisesRegex(TPE, r"Invalid loop variable name"):
            self._render('{% for é in a %}{% endfor %}')

        with self.assertRaisesRegex(TPE, r"Invalid loop variable name"):
            self._render('{% for é in a %}{% endfor %}')

        # Extend/block errors
        with self.assertRaisesRegex(TPE, r"An extend directive can only be "):
            self._render('abc {% extend "test.html" %}')

        with self.assertRaisesRegex(NameError, r"Duplicate block name "):
            self._render('{% block "a" %}{% block "b" %}'
                '{% block "a" %}{% endblock %}{% endblock %}{% endblock %}')

        with self.assertRaisesRegex(TPE, r"Expected endblock"):
            self._render('{% block "x" %}{% endif %}')

        with self.assertRaisesRegex(TPE, r"Did not find endblock for this "
            "block"):
            self._render('{% block "x" %}')

        base_tpl_path = pathlib.Path(self._temp_dir1.name) / 'base.html'

        try:
            base_tpl_path.write_text('{% block "x" %}{% endblock %}'
                '{% block "y" %}{% endblock %}')

            with self.assertRaisesRegex(TPE, r"Expected endblock"):
                tpl = '{% extend "base.html" %}{% block "x" %}{% endif %}'
                self._render(tpl)

            with self.assertRaisesRegex(TPE, r"Did not find endblock for this "
                "block"):
                self._render('{% extend "base.html" %}{% block "x" %}')

            with self.assertRaisesRegex(TPE, r"Block name 'z' is not defined"):
                tpl = '{% extend "base.html" %}{% block "z" %}{% endblock %}'
                self._render(tpl)

            with self.assertRaisesRegex(TPE, r"The block 'y' contained in "):
                tpl = ('{% extend "base.html" %}{% block "x" %}{% block "y" %}'
                    '{% endblock %}{% endblock %}')
                self._render(tpl)

            with self.assertRaisesRegex(TPE, r"Block name 'z' is not defined"):
                tpl = '{% extend "base.html" %}{% block "z" %}{% endblock %}'
                self._render(tpl)

            with self.assertRaisesRegex(TPE, r"An extending template can only"):
                tpl = '{% extend "base.html" %}{% block "x" %}{% endblock %}abc'
                self._render(tpl)

        finally:
            base_tpl_path.unlink(missing_ok=True)

    @classmethod
    def _render(cls, template_text, variables=None):
        if variables is None:
            variables = {}

        linecache.clearcache()

        base_path = pathlib.Path(cls._temp_dir1.name)
        ovr_path = pathlib.Path(cls._temp_dir2.name)

        env = {
            'TPL_OVERRIDE_DIR': str(ovr_path)
        }

        with patch('pyshellytemp.tpl_mgr.TPL_DIR', base_path):
            with patch('pyshellytemp.tpl_mgr.os.environ', env):
                mgr = TemplateManager.create()

        file_name = 'template.txt'
        file_path = base_path / 'template.txt'
        file_path.write_text(template_text)

        with patch('pyshellytemp.tpl_mgr.templates', mgr):
            return mgr.get(file_name).render(variables)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._temp_dir1 = tempfile.TemporaryDirectory('test_template_1')
        cls._temp_dir2 = tempfile.TemporaryDirectory('test_template_2')

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._temp_dir1.cleanup()
        cls._temp_dir2.cleanup()
