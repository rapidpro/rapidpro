from __future__ import print_function, unicode_literals

import ply.lex as lex
import pytz
import re
import six

from collections import OrderedDict
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from ply import yacc
from temba.locations.models import AdminBoundary
from temba.utils import str_to_datetime
from temba.values.models import Value

PROPERTY_ALIASES = None  # initialised in contact_search to avoid circular import

NON_FIELD_PROPERTIES = ('name', 'urns__path')  # identifiers which are not contact fields

TEXT_LOOKUP_ALIASES = LOCATION_LOOKUP_ALIASES = {
    '=': 'iexact',
    'is': 'iexact',
    '~': 'icontains',
    'has': 'icontains'
}

DECIMAL_LOOKUP_ALIASES = {
    '=': 'exact',
    'is': 'exact',
    '>': 'gt',
    '>=': 'gte',
    '<': 'lt',
    '<=': 'lte'
}

DATETIME_LOOKUP_ALIASES = {
    '=': '<equal>',
    'is': '<equal>',
    '>': 'gt',
    '>=': 'gte',
    '<': 'lt',
    '<=': 'lte'
}


class SearchException(Exception):
    """
    Exception class for unparseable search queries
    """
    def __init__(self, message):
        self.message = message


class SearchLexer(object):
    """
    Lexer for complex search queries
    """
    t_LPAREN = r'\('
    t_RPAREN = r'\)'
    t_ignore = ' \t'  # ignore tabs and spaces

    tokens = ('AND', 'OR', 'COMPARATOR', 'TEXT', 'STRING', 'LPAREN', 'RPAREN')

    literals = '()'

    # treat reserved words specially http://www.dabeaz.com/ply/ply.html#ply_nn4
    reserved = {
        'and': 'AND',
        'or': 'OR',
        'has': 'COMPARATOR',
        'is': 'COMPARATOR',
    }

    def __init__(self, **kwargs):
        self.lexer = lex.lex(module=self, reflags=re.UNICODE, **kwargs)

    def input(self, s):
        return self.lexer.input(s)

    def token(self):
        return self.lexer.token()

    def t_COMPARATOR(self, t):
        r"""(?i)~|=|[<>]=?|~~?"""
        return t

    def t_STRING(self, t):
        r"""("[^"]*")"""
        t.value = t.value[1:-1]
        return t

    def t_TEXT(self, t):
        r"""[\w_\.\+\-\/]+"""
        t.type = self.reserved.get(t.value.lower(), 'TEXT')
        return t

    def t_error(self, t):
        raise SearchException("Invalid character %s" % t.value[0])

    def test(self, data):  # pragma: no cover
        self.lexer.input(data)
        while True:
            tok = self.lexer.token()
            if not tok:
                break
            print(tok)


class QueryNode(object):
    def simplify(self):
        return self

    def split_by_field(self):
        return self


@six.python_2_unicode_compatible
class FieldCondition(QueryNode):
    def __init__(self, field, comparator, value):
        self.field = field
        self.comparator = comparator
        self.value = value

    def __str__(self):
        return '%s%s%s' % (self.field, self.comparator, self.value)


@six.python_2_unicode_compatible
class BoolCombination(QueryNode):
    AND = 'and'
    OR = 'or'

    def __init__(self, op, *args):
        self.op = op
        self.args = list(args)

    def simplify(self):
        """
        The expression `x OR y OR z` will be parsed as `OR(OR(x, y), z)` but because the logical operators AND/OR are
        associative we can simplify this as `OR(x, y, z)`.
        """
        self.args = [a.simplify() for a in self.args]  # simplify our arguments first

        simplified = []

        for arg in self.args:
            if isinstance(arg, FieldCondition):
                simplified.append(arg)
            elif arg.op != self.op:
                # can't optimize if args are a different boolean op
                return self
            else:
                simplified += arg.args

        return BoolCombination(self.op, *simplified)

    def is_single_field(self):
        """
        Checks whether this is a combination of conditions on the same field, which can be optimized
        """
        fields = set()
        for a in self.args:
            if not isinstance(a, FieldCondition):
                return False
            fields.add(a.field)

        return len(fields) == 1

    def split_by_field(self):
        if self.is_single_field():
            return self

        args_by_field = OrderedDict()
        for a in self.args:
            field = a.field if isinstance(a, FieldCondition) else None
            if field not in args_by_field:
                args_by_field[field] = []
            args_by_field[field].append(a)

        new_args = []
        for field, args in args_by_field.items():
            if len(args) > 1:
                new_args.append(BoolCombination(self.op, *args))
            else:
                new_args.append(args[0])

        if len(new_args) == 1:
            return new_args[0]

        return BoolCombination(self.op, *new_args)

    def __str__(self):
        return '%s(%s)' % (self.op.upper(), ', '.join([six.text_type(a) for a in self.args]))


# ================================== Parser definition ==================================

precedence = (
    ('left', 'OR'),
    ('left', 'AND'),
)


def p_expression_and(p):
    """expression : expression AND expression"""
    p[0] = BoolCombination(BoolCombination.AND, p[1], p[3])


def p_expression_or(p):
    """expression : expression OR expression"""
    p[0] = BoolCombination(BoolCombination.OR, p[1], p[3])


def p_expression_grouping(p):
    """expression : LPAREN expression RPAREN"""
    p[0] = p[2]


def p_expression_comparison(p):
    """expression : TEXT COMPARATOR literal"""
    p[0] = FieldCondition(p[1].lower(), p[2].lower(), p[3])


def p_literal(p):
    """literal : TEXT
               | STRING"""
    p[0] = p[1]


def p_error(p):
    message = ("Syntax error at '%s'" % p.value) if p else "Syntax error"
    raise SearchException(message)


search_lexer = SearchLexer()
tokens = search_lexer.tokens
search_parser = yacc.yacc(write_tables=False)


def parse_query(text):
    return search_parser.parse(text, lexer=search_lexer)
