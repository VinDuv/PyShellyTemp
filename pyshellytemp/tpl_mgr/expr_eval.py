"""
Template expression evaluator
"""

import ast
import os
import logging
import typing

from .tokenizer import Token

StrDict: typing.TypeAlias = dict[str, typing.Any]
BinOp: typing.TypeAlias = typing.Callable[[typing.Any, typing.Any], typing.Any]
UnOp: typing.TypeAlias = typing.Callable[[typing.Any], typing.Any]

LOGGER = logging.getLogger(__name__)
TPL_DEBUG = os.environ.get('TPL_DEBUG', '').lower() not in {'', 'n', 'no',
    'false'}

class ExpressionEvaluator:
    """
    Used to safely evaluate expressions in “if” and “for” blocks.
    """

    # Mappings between an AST operation, and the actual operation performed.
    BINOPS: typing.ClassVar[dict[typing.Type[ast.AST], BinOp]] = {
        ast.Add: lambda a, b: a + b,
        ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b,
        ast.FloorDiv: lambda a, b: a // b,
        ast.Pow: lambda a, b: a ** b,
    }

    UNOPS: typing.ClassVar[dict[typing.Type[ast.AST], UnOp]] = {
        ast.UAdd: lambda a: +a,
        ast.USub: lambda a: -a,
        ast.Not: lambda a: not a,
    }

    CMPOPS: typing.ClassVar[dict[typing.Type[ast.AST], BinOp]] = {
        ast.Eq: lambda a, b: a == b,
        ast.Gt: lambda a, b: a > b,
        ast.GtE: lambda a, b: a >= b,
        ast.In: lambda a, b: a in b,
        ast.Is: lambda a, b: a is b,
        ast.IsNot: lambda a, b: a is not b,
        ast.Lt: lambda a, b: a < b,
        ast.LtE: lambda a, b: a <= b,
        ast.NotEq: lambda a, b: a != b,
        ast.NotIn: lambda a, b: a not in b,
    }

    # Used to check if the expression only contains allowed nodes. Maps an
    # allowed node class to the sub-nodes to check.
    VALID_NODES: typing.ClassVar[dict[typing.Type[ast.AST], list[str]]] = {
        ast.UnaryOp: ['operand'],
        ast.BinOp: ['left', 'right'],
        ast.Name: [],
        ast.Attribute: ['value'],
        ast.Subscript: ['value', 'slice'],
        ast.Constant: [],
        ast.Compare: ['left', 'comparators']
    }

    _root_node: ast.AST

    def __init__(self, expr: str):
        """
        Initializes the expression evaluator with the provided expression.
        May raise a SyntaxError if the expression is incorrect or contains
        disallowed operations.
        """

        root_node = ast.parse(expr, mode='eval').body

        # Check the validity
        self._validate_node(expr, root_node)

        self._root_node = root_node

    def safe_eval(self, context: StrDict) -> typing.Any:
        """
        Evaluates the expression in the provided context.
        If an evaluation error occurs, None is returned.
        """

        try:
            return self._evaluate_node(self._root_node, context)
        except (KeyError, AttributeError, ValueError, TypeError, IndexError,
            ArithmeticError):
            if TPL_DEBUG:
                LOGGER.exception("Error evaluating %s in context %r",
                    ast.unparse(self._root_node), context)
            return None

    @classmethod
    def from_token(cls, token: Token, expr_start: int = 0) -> typing.Self:
        """
        Creates an expression evaluator from a token value.
        """

        try:
            return cls(token.value[expr_start:])
        except SyntaxError as err:
            assert err.offset is not None
            return token.raise_error(err.msg, offset=expr_start + err.offset -
                1)

    @staticmethod
    def token_value_to_string(token: Token) -> str:
        """
        Utility method that takes the value part of a token and interprets
        it as a quoted string.
        {% someblock "abc\xdef" %} => "abc\xdef" (string)
        """

        try:
            value_ast = ast.parse(token.value, mode='eval').body
        except SyntaxError as err:
            assert err.offset is not None
            return token.raise_error(err.msg, offset=err.offset)

        if (not isinstance(value_ast, ast.Constant) or
            not isinstance(value_ast.value, str)):
            return token.raise_error("Quoted string expected.", offset=0)

        return value_ast.value

    def __repr__(self) -> str:
        return f"<ExpressionEvaluator: {ast.unparse(self._root_node)}>"

    @classmethod
    def _validate_node(cls, text: str, node: ast.AST) -> None:
        """
        Check that the provided node (and its children) represent supported
        operations.
        """

        assert node.end_col_offset is not None

        to_check = cls.VALID_NODES.get(node.__class__)
        if to_check is None:
            name = node.__class__.__name__.lower()
            filename = '<expr>'
            lineno = 1
            offset = node.col_offset + 1
            end_lineno = 1
            end_offset = node.end_col_offset + 1

            raise SyntaxError(f"Invalid operation {name!r}", (filename, lineno,
                offset, text, end_lineno, end_offset))

        for attr in to_check:
            sub_node_or_list: ast.AST | list[ast.AST] = getattr(node, attr)
            if isinstance(sub_node_or_list, list):
                # Special case for the comparator, where the right-hand side
                # is a list of values to support chained comparisons (a < b < c)
                # Only support the 1-value case.
                try:
                    sub_node, = sub_node_or_list
                except ValueError:
                    filename = '<expr>'
                    lineno = 1
                    offset = node.col_offset + 1
                    end_lineno = 1
                    end_offset = node.end_col_offset + 1
                    raise SyntaxError("Comparison operations are limited to "
                        "two operands", (filename, lineno, offset, text,
                        end_lineno, end_offset)) from None
            else:
                sub_node = sub_node_or_list

            cls._validate_node(text, sub_node)

    @classmethod
    def _evaluate_node(cls, node: ast.AST, context: StrDict) -> typing.Any:
        class_name = node.__class__.__name__.lower()

        evaluator = getattr(cls, f'_evaluate_{class_name}', None)
        if evaluator is None:
            raise AssertionError(f"Unhandled node: {ast.dump(node)}")

        return evaluator(node, context)

    @classmethod
    def _evaluate_unaryop(cls, node: ast.UnaryOp,
        context: StrDict) -> typing.Any:
        operand = cls._evaluate_node(node.operand, context)

        return cls.UNOPS[node.op.__class__](operand)

    @classmethod
    def _evaluate_binop(cls, node: ast.BinOp, context: StrDict) -> typing.Any:
        left = cls._evaluate_node(node.left, context)
        right = cls._evaluate_node(node.right, context)

        return cls.BINOPS[node.op.__class__](left, right)

    @classmethod
    def _evaluate_compare(cls, node: ast.Compare,
        context: StrDict) -> typing.Any:
        left = cls._evaluate_node(node.left, context)
        node_right, = node.comparators # length is 1 from _validate_node
        node_op, = node.ops
        right = cls._evaluate_node(node_right, context)

        return cls.CMPOPS[node_op.__class__](left, right)

    @classmethod
    def _evaluate_name(cls, node: ast.Name, context: StrDict) -> typing.Any:
        return context[node.id]

    @classmethod
    def _evaluate_attribute(cls, node: ast.Attribute,
        context: StrDict) -> typing.Any:
        value = cls._evaluate_node(node.value, context)
        return getattr(value, node.attr)

    @classmethod
    def _evaluate_subscript(cls, node: ast.Subscript,
        context: StrDict) -> typing.Any:
        value = cls._evaluate_node(node.value, context)
        slice_val = cls._evaluate_node(node.slice, context)
        return value[slice_val]

    @classmethod
    def _evaluate_constant(cls, node: ast.Constant,
        _context: StrDict) -> typing.Any:
        return node.value
