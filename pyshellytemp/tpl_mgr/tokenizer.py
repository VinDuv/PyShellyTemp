"""
Template tokenizer
"""

import dataclasses
import enum
import linecache
import pathlib
import re
import typing


@dataclasses.dataclass(frozen=True)
class Token:
    """
    Represents a token parsed in the document.
    """

    # The items defined in the match expression must match the Type enumeration
    # below.
    TOKEN_MATCH_RE: typing.ClassVar[typing.Pattern[str]] = re.compile(r"""
         {{\s*(?P<var_value>[a-zA-Z0-9 +/\[\]<>._-]+?)\s*}} # value
        |{%\s*(?P<type>[a-z]+)\s*(?P<value>.*?)\s*%} # block
    """, re.VERBOSE)

    class Type(enum.Enum):
        """
        The type of a token.
        """

        # Only tokens values matching [a-z]+ can be formed from the template.
        # This means that tokens that shouldn’t be allowed in the template, like
        # EOF, should contain non-alphabetic characters in their value.

        TEXT = 'text'  # Regular text
        VALUE = 'value'  # Value inclusion
        EXTEND = 'extend'  # Template extend marker
        IF = 'if'  # If start
        FOR = 'for'  # for ... in start
        ELSE = 'else'  # else start
        BLOCK = 'block'  # block start
        ENDIF = 'endif'  # if end
        ENDFOR = 'endfor'  # for end
        ENDBLOCK = 'endblock'  # block end
        EOF = 'end-of-file'  # End of file

    class ParseError(SyntaxError):
        """
        Used to report parse errors in the template.
        """

    # Token properties: type and location
    type: Type
    file_path: pathlib.Path
    lineno: int
    offset: int
    val_offset: int
    value: str

    def raise_error(self, message: str, offset: int=-1) -> typing.Never:
        """
        Raises a parse error with the specified message and the file/lineno
        recorded in the token.
        offset is the offset of the error relative to the value; can be 0 if
        the error is at the start of the value. If not specified, the error
        is signaled at the start of the token.
        """

        # The Python error offset starts at 1, not 0
        if offset < 0:
            err_pos = self.offset + 1
        else:
            err_pos = self.val_offset + offset + 1

        file_path = str(self.file_path)
        lineno = self.lineno
        line_text = linecache.getline(file_path, self.lineno)

        err = self.ParseError(message, (file_path, lineno, err_pos, line_text))
        raise err from None

    @classmethod
    def tokenize_fdesc(cls, path: pathlib.Path,
        fdesc: typing.TextIO) -> 'TokGen':
        """
        Yields tokens read from the file descriptor.
        """

        lineno = 0  # Initialized for the eof token if the file is empty

        for lineno, line in enumerate(fdesc, 1):
            yield from cls._tokenize_line(path, lineno, line)

        yield cls(cls.Type.EOF, path, lineno, 1, 1, '')

    @classmethod
    def _tokenize_line(cls, path: pathlib.Path, lineno: int,
        line: str) -> typing.Iterator[typing.Self]:
        """
        Tokenizes one line of input.
        """

        cur_offset = 0
        for match in cls.TOKEN_MATCH_RE.finditer(line):
            tok_offset = match.start()
            if tok_offset > cur_offset:
                # Yield the text between two tokens
                yield cls(cls.Type.TEXT, path, lineno, cur_offset, cur_offset,
                    line[cur_offset:tok_offset])

            groups = match.groupdict()
            var_value = groups['var_value']
            if var_value is not None:
                tok_type = cls.Type.VALUE
                tok_val = var_value
                tok_val_offset = match.start('var_value')
            else:
                tok_raw_type = groups['type']
                try:
                    tok_type = cls.Type(tok_raw_type)
                except ValueError:
                    type_offset = match.start('type')
                    raise cls.ParseError(f"Unknown tag {tok_raw_type!r}",
                        (path, lineno, type_offset + 1, line)) from None
                tok_val = groups['value']
                tok_val_offset = match.start('value')

            # Now we should have found the right token
            yield cls(tok_type, path, lineno, tok_offset, tok_val_offset,
                tok_val)

            cur_offset = match.end()

        # Yield the text at the end of the line (There should always be a
        # newline)
        yield cls(cls.Type.TEXT, path, lineno, cur_offset, cur_offset,
            line[cur_offset:])


# A generator of tokens
TokGen: typing.TypeAlias = typing.Generator[Token, None, None]
