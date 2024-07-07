"""
A simple, Django-like template manager.
Templates are stored in a “templates” directory at the same location as this
package. They can optionally be overriden (on a per-file basis) by templates
located in the TPL_OVERRIDE_DIR environment variable.
"""

import abc
import dataclasses
import html
import os
import pathlib
import re
import sys
import threading
import typing

from .expr_eval import ExpressionEvaluator
from .tokenizer import Token, TokGen

__all__ = ['templates', 'SafeString']

TPL_DIR = pathlib.Path(__file__).parent.parent.resolve() / 'templates'

StrDict: typing.TypeAlias = dict[str, typing.Any]


class SafeString(str):
    """
    Wrapper around a string to make it render without HTML escaping.
    """

    def to_html(self) -> str:
        """
        Returns an HTML version of the object (here, return the string without
        modification)
        """

        return self

    def __repr__(self) -> str:
        str_repr = super().__repr__()
        return f"SafeString({str_repr})"

# Maps a template 'block' item by its name
BlockMap: typing.TypeAlias = dict[str, 'TemplateBlock']

class TemplateItem(abc.ABC):
    """
    Base class for template items.
    """

    # Handlers for the tokens found while parsing a container. If the handler
    # is None, it indicates that the end of the container block has been
    # reached.
    # The handlers are registered by their respective classes.
    _handlers: typing.ClassVar[dict[Token.Type, typing.Union[
        type['TemplateItem'], None, 'ellipsis']]] = {
        Token.Type.TEXT: ...,
        Token.Type.VALUE: ...,
        Token.Type.EXTEND: ...,
        Token.Type.IF: ...,
        Token.Type.FOR: ...,
        Token.Type.BLOCK: ...,
        Token.Type.ELSE: None,
        Token.Type.ENDIF: None,
        Token.Type.ENDFOR: None,
        Token.Type.ENDBLOCK: None,
        Token.Type.EOF: None,
    }

    @classmethod
    def __init_subclass__(cls) -> None:
        """
        Used to automatically register token handlers when a TemplateItem
        subclass is declared.
        """

        super().__init_subclass__()

        handled_token = cls.__dict__.get('HANDLED_TOKEN')
        if handled_token is not None:
            assert cls._handlers[handled_token] is ...

            cls._handlers[handled_token] = cls

    @abc.abstractmethod
    def render_iter(self, context: StrDict) -> typing.Iterator[str]:
        """
        Yields rendered parts of the template, that must be joined together
        to form the final render.
        """

    def get_block_name_map_into(self, _names: BlockMap) -> None:
        """
        Fills up the provided dictionary, associating a block name with each
        named block contained in this template item.
        This also checks the unicity of the block names; if a duplicate name is
        found, a NameError exception is raised.
        """

        # This default implementation does nothing since a template item
        # cannot usually contain named blocks.

    @classmethod
    @abc.abstractmethod
    def create(cls, token: Token, rest: TokGen,
        idx: int | None = None) -> tuple[typing.Self, Token]:
        """
        Creates the template item by reading the token and possibly the token
        generator.
        idx indicates the index of the template item in the containing block.
        Returns the created instance and the token following it.
        """


class TemplateText(TemplateItem):
    """
    Normal text found in the template. When rendered, the text is put verbatim
    in the output.
    """

    HANDLED_TOKEN = Token.Type.TEXT

    def __init__(self, text: str):
        self._text = text

    def render_iter(self, context: StrDict) -> typing.Iterator[str]:
        yield self._text

    @classmethod
    def create(cls, token: Token, rest: TokGen,
        idx: int | None = None) -> tuple[typing.Self, Token]:
        text_parts = []

        # Consume all the text tokens (will at least run once)
        while token.type is cls.HANDLED_TOKEN:
            text_parts.append(token.value)
            token = next(rest)

        if idx == 0 and text_parts[0][:1] == '\n':
            # Remove the start newline of the text block if it is the first
            # item of a container.
            text_parts[0] = text_parts[0][1:]

        return cls("".join(text_parts)), token

    def __repr__(self) -> str:
        text = self._text[:32] + ('...' if self._text[32:] else '')
        return f"<TemplateText: {text!r}>"


class TemplateValue(TemplateItem):
    """
    Variable value. When rendered, the value is replaced by values found in the
    context.
    if the value has a to_html() method, it is called and the result string
    is used. If not, it is converted to a string and HTML escaped. Use
    SafeString to return a string without escaping.
    If a value is missing, nothing is rendered in the template.
    """

    HANDLED_TOKEN = Token.Type.VALUE

    def __init__(self, expr: ExpressionEvaluator):
        self._expr = expr

    def render_iter(self, context: StrDict) -> typing.Iterator[str]:
        value = self._expr.safe_eval(context)
        if value is None:
            return

        try:
            value = str(value.to_html())
        except AttributeError:
            value = html.escape(str(value))

        yield value

    @classmethod
    def create(cls, token: Token, rest: TokGen,
        idx: int | None = None) -> tuple[typing.Self, Token]:
        expr = ExpressionEvaluator.from_token(token)

        return cls(expr), next(rest)

    def __repr__(self) -> str:
        return f"<TemplateValue: {self._expr!r}>"


class TemplateContainer(TemplateItem):
    """
    Part of the template that contains other parts; for instance, the contents
    of an if or for template item.
    """

    def __init__(self, items: list[TemplateItem]):
        self._items = items

    def render_iter(self, context: StrDict) -> typing.Iterator[str]:
        for item in self._items:
            yield from item.render_iter(context)

    def get_block_name_map_into(self, names: BlockMap) -> None:
        for item in self._items:
            item.get_block_name_map_into(names)

    @classmethod
    def create(cls, token: Token, rest: TokGen,
        idx: int | None = None) -> tuple[typing.Self, Token]:
        items, token = cls._get_inner_items(rest, initial=token)

        return cls(items), token

    @classmethod
    def from_tokens(cls, tokens: TokGen) -> tuple[typing.Self, Token]:
        """
        Returns a container generated by consuming the tokens from the specified
        generator.
        This method works like create() but does not take a starting token.
        """

        items, token = cls._get_inner_items(tokens)

        return cls(items), token

    @classmethod
    def _get_inner_items(cls, tokens: TokGen,
        initial: Token | None = None) -> tuple[list[TemplateItem],
        Token]:
        """
        Parses the given tokens into template items, until an end token is
        encountered and was not consumed by a template item.
        Returns the parsed items and the end token.
        """

        items: list[TemplateItem] = []

        cur_idx = 0
        if initial is None:
            token = next(tokens)
        else:
            token = initial
        rest = tokens
        while True:
            handler = cls._handlers[token.type]
            assert handler is not ..., f"No handler for {token.type}"

            if handler is None:
                break

            prev_token = token
            item, token = handler.create(prev_token, rest, cur_idx)

            # The sub-item should at least has consumed its token
            assert token is not prev_token, (token, prev_token)

            items.append(item)
            cur_idx += 1

        return items, token

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}: {self._items}>"


class TemplateFor(TemplateItem):
    """
    Handles template loops. When rendered, the expression after the “in” is
    evaluated, and the resulting object is iterated through. At each iteration,
    the loop body is executed with the variables set to the iteration items.
    If an empty iterator is returned, the else block is rendered instead.
    If the expression does not returns an iterable, nothing is rendered.
    """

    HANDLED_TOKEN = Token.Type.FOR
    VAR_NAME_RE = re.compile(r'\s*([A-Za-z_][A-Za-z0-9_]*)\s*')

    def __init__(self, expr: ExpressionEvaluator, variables: str | list[str],
        loop_body: TemplateContainer, else_body: TemplateContainer | None):
        self._expr = expr
        self._vars = variables
        self._loop_body = loop_body
        self._else_body = else_body

    def render_iter(self, context: StrDict) -> typing.Iterator[str]:
        try:
            iterable = iter(self._expr.safe_eval(context))
        except TypeError:
            iterable = ()

        exec_else = True

        if isinstance(self._vars, list):
            for item in iterable:
                exec_else = False

                for var_name, value in zip(self._vars, item):
                    context[var_name] = value

                yield from self._loop_body.render_iter(context)

        else:
            for item in iterable:
                exec_else = False

                context[self._vars] = item

                yield from self._loop_body.render_iter(context)

        if exec_else and self._else_body is not None:
            yield from self._else_body.render_iter(context)

    @classmethod
    def create(cls, token: Token, rest: TokGen,
        idx: int | None = None) -> tuple[typing.Self, Token]:
        for_str = token.value

        in_pos = for_str.find(' in ')
        if in_pos <= 0:
            token.raise_error("Expected: var[, var...] in value", offset=0)

        vars_str = for_str[:in_pos]
        expr_start = in_pos + 4

        variables: str | list[str]

        raw_vars = vars_str.split(',')
        if len(raw_vars) == 1:
            # Single variable
            variables = cls._validate_var_name(token, raw_vars[0])
        elif not raw_vars[-1].strip():
            # Comma at the end of the variable list; allow it, it allows
            # 1-tuple destructuring
            variables = [cls._validate_var_name(token, raw_var)
                for raw_var in raw_vars[:-1]]
        else:
            variables = [cls._validate_var_name(token, raw_var)
                for raw_var in raw_vars]

        expr = ExpressionEvaluator.from_token(token, expr_start)

        loop_body, next_token = TemplateContainer.from_tokens(rest)
        if next_token.type is Token.Type.ELSE:
            else_body, next_token = TemplateContainer.from_tokens(rest)
        else:
            else_body = None

        if next_token.type is not Token.Type.ENDFOR:
            if next_token.type is Token.Type.EOF:
                token.raise_error("Did not find endfor for this for block")
            next_token.raise_error(f"Expected endfor to close for block "
                f"started at line {token.lineno}")

        next_token = next(rest)

        return cls(expr, variables, loop_body, else_body), next_token

    def get_block_name_map_into(self, names: BlockMap) -> None:
        self._loop_body.get_block_name_map_into(names)
        if self._else_body is not None:
            self._else_body.get_block_name_map_into(names)

    def __repr__(self) -> str:
        return (f"<TemplateFor expr {self._expr!r} vars {self._vars} loop "
            f"{self._loop_body} else {self._else_body}>")

    @classmethod
    def _validate_var_name(cls, token: Token, raw_var_name: str) -> str:
        """
        Cleans up and validates the name of loop variable.
        """

        match = cls.VAR_NAME_RE.match(raw_var_name)
        if match:
            return match.group(1)

        return token.raise_error(f"Invalid loop variable name {raw_var_name!r}",
            offset=0)


class TemplateIf(TemplateItem):
    """
    Handles template conditions. When rendered, the expression specified is
    evaluated. If it’s true, the block after the if is rendered. If it’s false,
    the block after the else is rendered, if present.
    """

    HANDLED_TOKEN = Token.Type.IF

    def __init__(self, expr: ExpressionEvaluator, body: TemplateContainer,
        else_body: TemplateContainer | None):
        self._expr = expr
        self._body = body
        self._else_body = else_body

    def render_iter(self, context: StrDict) -> typing.Iterator[str]:
        if self._expr.safe_eval(context):
            yield from self._body.render_iter(context)
        elif self._else_body is not None:
            yield from self._else_body.render_iter(context)

    def get_block_name_map_into(self, names: BlockMap) -> None:
        self._body.get_block_name_map_into(names)
        if self._else_body is not None:
            self._else_body.get_block_name_map_into(names)

    @classmethod
    def create(cls, token: Token, rest: TokGen,
        idx: int | None = None) -> tuple[typing.Self, Token]:
        expr = ExpressionEvaluator.from_token(token, 0)

        body, next_token = TemplateContainer.from_tokens(rest)
        if next_token.type is Token.Type.ELSE:
            else_body, next_token = TemplateContainer.from_tokens(rest)
        else:
            else_body = None

        if next_token.type is not Token.Type.ENDIF:
            if next_token.type is Token.Type.EOF:
                token.raise_error("Did not find endif for this if block")
            next_token.raise_error(f"Expected endif to close if block "
                f"started at line {token.lineno}")

        next_token = next(rest)

        return cls(expr, body, else_body), next_token

    def __repr__(self) -> str:
        return (f"<TemplateIf expr {self._expr!r} body {self._body} "
            f"else {self._else_body}>")


class TemplateBlock(TemplateContainer):
    """
    A named block item. Acts like a normal container, but has a name.
    """

    HANDLED_TOKEN = Token.Type.BLOCK

    class TemplateExtendReject(TemplateContainer):
        """
        Rejects the {% extend %} token if it appears in the document. This
        token is only supposed to be consumed directly by the Template class
        constructor.
        """

        HANDLED_TOKEN = Token.Type.EXTEND

        @classmethod
        def create(cls, token: Token, _rest: TokGen,
            _idx: int | None = None) -> tuple[typing.Self, Token]:
            token.raise_error("An extend directive can only be present at "
                "the very start of the file.")

    def __init__(self, name: str, items: list[TemplateItem]):
        super().__init__(items)
        self._name = name

    def get_block_name_map_into(self, names: BlockMap) -> None:
        # Propagate the check to inner blocks
        super().get_block_name_map_into(names)

        if self._name in names:
            raise NameError(self._name)

        names[self._name] = self

    def replace_block_contents(self, new_items: list[TemplateItem]) -> tuple[
        typing.Iterable[str], BlockMap]:
        """
        Replace the block contents with new items.

        Returns the names of the sub-blocks that were removed by the process,
        and the names => values of sub-blocks that were added.
        """

        old_names: BlockMap = {}

        # Note that since super() is used, this calls TemplateContainer’s
        # implementation, which does not put self into the result dict
        super().get_block_name_map_into(old_names)
        self._items = new_items

        new_names: BlockMap = {}
        for new_item in new_items:
            new_item.get_block_name_map_into(new_names)

        return old_names.keys(), new_names

    @classmethod
    def create(cls, token: Token, rest: TokGen,
        idx: int | None = None) -> tuple[typing.Self, Token]:
        name = ExpressionEvaluator.token_value_to_string(token)

        items, next_token = cls._get_inner_items(rest)

        if next_token.type is not Token.Type.ENDBLOCK:
            if next_token.type is Token.Type.EOF:
                token.raise_error("Did not find endblock for this block")
            next_token.raise_error(f"Expected endblock to close block "
                f"started at line {token.lineno}")

        next_token = next(rest)

        return cls(name, items), next_token



class Template(TemplateContainer):
    """
    The full template for a file.
    """

    @classmethod
    def from_fdesc(cls, path: pathlib.Path, fdesc: typing.TextIO) -> tuple[
        'Template', BlockMap]:
        """
        Create a template by reading a file descriptor. Returns the template
        and its block mapping.
        """

        tokens = Token.tokenize_fdesc(path, fdesc)
        token = next(tokens)
        if token.type is Token.Type.EXTEND:
            rel_path = ExpressionEvaluator.token_value_to_string(token)
            template, name_mapping = templates.load_uncached(rel_path,
                path)
            cls._extend_template(name_mapping, tokens)
            return template, name_mapping

        template, end_token = cls.create(token, tokens)

        if end_token.type is not Token.Type.EOF:
            end_token.raise_error("Unexpected close tag")

        name_mapping = {}
        try:
            template.get_block_name_map_into(name_mapping)
        except NameError as err:
            dup = str(err)
            raise NameError(f"Template {path}: Duplicate block name "
                f"{dup!r}") from None

        return template, name_mapping

    def render(self, context: StrDict) -> str:
        """
        Renders the template item, with the specified context.
        """

        return "".join(self.render_iter(self.COWDict(context)))

    @classmethod
    def _extend_template(cls, block_names: BlockMap,
        tokens: TokGen) -> None:
        """
        Extends a template by overriding its named blocks with the blocks
        loaded from the specified tokens.
        The block_names parameter contains the named block mapping for the
        template, and is modified in place while the template is extended.
        """

        token = next(tokens)
        while token.type is not Token.Type.EOF:
            if token.type is Token.Type.TEXT and not token.value.strip():
                # Ignore blank text between blocks
                pass

            elif token.type is Token.Type.BLOCK:
                block_name = ExpressionEvaluator.token_value_to_string(token)

                new_items, end_token = cls._get_inner_items(tokens)
                if end_token.type is not Token.Type.ENDBLOCK:
                    if end_token.type is Token.Type.EOF:
                        token.raise_error("Did not find endblock for this "
                            "block")
                    end_token.raise_error(f"Expected endblock to close "
                        f"block started at line {token.lineno}")

                block = block_names.get(block_name)
                if block is None:
                    token.raise_error(f"Block name {block_name!r} is not "
                        f"defined by the extended template (or was removed by "
                        f"block replacement).")

                old_names, new_names = block.replace_block_contents(new_items)
                for old_name in old_names:
                    # Should never raise, unless the names got desynced somehow
                    del block_names[old_name]

                for new_name, block in new_names.items():
                    if new_name in block_names:
                        token.raise_error(f"The block {new_name!r} contained "
                            f"in this block is already defined in a previous "
                            "block.")
                    block_names[new_name] = block

            else:
                token.raise_error("An extending template can only contain "
                    "{% block %} elements at upper level")

            token = next(tokens)

    class COWDict(StrDict):
        """
        Manages a copy of the values dictionary. Copy-on-write is used: As long
        as a value is not written to, the original dictionary value is used.
        """

        def __init__(self, values: StrDict):
            self._values = values

        def __missing__(self, key: str) -> typing.Any:
            return self._values[key]

        def __repr__(self) -> str:
            cur_values = {}
            cur_values.update(self._values)
            cur_values.update(self)
            return repr(cur_values)


@dataclasses.dataclass(frozen=True)
class TemplateManager:
    """
    Manages template loading and caching.
    """

    tpl_dir: pathlib.Path
    ovr_dir: pathlib.Path | None = None
    _cache: dict[str, Template] = dataclasses.field(default_factory=dict)
    _lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)

    def get(self, rel_path: str) -> Template:
        """
        Return the compiled template from the specified relative path.
        The relative path is not validated or normalized so this function should
        only be called with static, hard-coded paths.
        The resulting template is cached for future use.
        """

        template = self._cache.get(rel_path)
        if template is not None:
            return template

        with self._lock:
            template = self._cache.get(rel_path)
            if template is None:
                template, _ = self.load_uncached(rel_path)
                self._cache[rel_path] = template

            return template

    def load_uncached(self, rel_path: str,
        extender_path: pathlib.Path | None = None) -> typing.Tuple[Template,
        BlockMap]:
        """
        Load and compile a template, but do not put it in the cache. The
        resulting template object can be modified, which is used by the
        template extension mechanism.

        extender_path is the path of the template whose {% extend %} caused this
        load (if any). This is used to allow an override to extend a template
        with the same relative path; in that case the base template (in the
        non-override directory) is returned by this function.

        Returns the loaded template and its block mapping.
        """

        if self.ovr_dir is not None:
            path = self.ovr_dir / rel_path
            if path != extender_path:
                try:
                    with path.open('r') as fdesc:
                        return Template.from_fdesc(path, fdesc)
                except FileNotFoundError:
                    pass

        path = self.tpl_dir / rel_path
        with path.open('r') as fdesc:
            return Template.from_fdesc(path, fdesc)

    @classmethod
    def create(cls) -> typing.Self:
        """
        Creates the default template manager.
        """

        raw_path = os.environ.get('TPL_OVERRIDE_DIR')
        if raw_path is None:
            ovr_dir = None
        else:
            ovr_dir = pathlib.Path(raw_path)

            if not ovr_dir.is_dir():
                sys.exit(f"Template override directory {raw_path} is not "
                    f"accessible or not a directory.")

            ovr_dir = ovr_dir.resolve()

        return cls(TPL_DIR, ovr_dir)


templates = TemplateManager.create()
