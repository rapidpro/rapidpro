from __future__ import print_function, unicode_literals

import ply.lex as lex
import pytz
import operator
import re
import six

from collections import OrderedDict
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from functools import reduce
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


@six.python_2_unicode_compatible
class ContactQuery(object):
    def __init__(self, root):
        self.root = root.simplify().split_by_prop()

    def as_query(self, org):
        prop_map = self.get_prop_map(org)

        return self.root.as_query(prop_map)

    def get_prop_map(self, org):
        from temba.contacts.models import ContactField

        prop_map = {p: None for p in self.root.get_prop_names()}

        for field in ContactField.objects.filter(org=org, key__in=prop_map.keys(), is_active=True):
            prop_map[field.key] = field

        # TODO schemes, attributes

        for prop, prop_obj in prop_map.items():
            if not prop_obj:
                raise SearchException("Unrecognized field: %s" % prop)

        return prop_map

    def __str__(self):
        return six.text_type(self.root)


class QueryNode(object):
    def simplify(self):
        return self

    def split_by_prop(self):
        return self

    def as_query(self, prop_map):
        pass


@six.python_2_unicode_compatible
class Condition(QueryNode):
    def __init__(self, prop, comparator, value):
        self.prop = prop
        self.comparator = comparator
        self.value = value

    def get_prop_names(self):
        return [self.prop]

    def as_query(self, prop_map):
        from temba.contacts.models import ContactField

        prop_obj = prop_map[self.prop]

        if isinstance(prop_obj, ContactField):
            value_query = self.get_value_query(prop_obj)

            return Q(id__in=Value.objects.filter(**value_query).values('contact_id'))
        else:
            # TODO if not a field?
            raise ValueError("TODO")

    def get_value_query(self, field):
        # TODO different value types, lookups

        lookup = 'iexact'
        return {'contact_field': field, 'string_value__%s' % lookup: self.value}

    def __str__(self):
        return '%s%s%s' % (self.prop, self.comparator, self.value)


@six.python_2_unicode_compatible
class BoolCombination(QueryNode):
    """
    A combination of two or more conditions using an AND or OR logical operation
    """
    def __init__(self, op, *children):
        self.op = op
        self.children = list(children)

    def get_prop_names(self):
        names = []
        for child in self.children:
            names += child.get_prop_names()
        return names

    def simplify(self):
        """
        The expression `x OR y OR z` will be parsed as `OR(OR(x, y), z)` but because the logical operators AND/OR are
        associative we can simplify this as `OR(x, y, z)`.
        """
        self.children = [c.simplify() for c in self.children]  # simplify our children first

        simplified = []

        for child in self.children:
            if isinstance(child, Condition):
                simplified.append(child)
            elif child.op != self.op:
                return self  # can't optimize if children are combined with a different boolean op
            else:
                simplified += child.children

        return BoolCombination(self.op, *simplified)

    def split_by_prop(self):
        """
        The expression `OR(a=1, b=2, a=3)` can be re-arranged to `OR(OR(a=1, a=3), b=2)` so that `a=1 OR a=3` can be
        more efficiently checked using a single query on `a`.
        """
        self.children = [c.split_by_prop() for c in self.children]  # split our children first

        children_by_prop = OrderedDict()
        for child in self.children:
            prop = child.prop if isinstance(child, Condition) else None
            if prop not in children_by_prop:
                children_by_prop[prop] = []
            children_by_prop[prop].append(child)

        new_children = []
        for prop, children in children_by_prop.items():
            if len(children) > 1:
                new_children.append(SinglePropCombination(prop, self.op, *children))
            else:
                new_children.append(children[0])

        if len(new_children) == 1:
            return new_children[0]

        return BoolCombination(self.op, *new_children)

    def as_query(self, prop_map):
        return reduce(self.op, [child.as_query(prop_map) for child in self.children])

    def __str__(self):
        op = 'OR' if self.op == operator.or_ else 'AND'
        return '%s(%s)' % (op, ', '.join([six.text_type(c) for c in self.children]))


@six.python_2_unicode_compatible
class SinglePropCombination(BoolCombination):
    """
    A special case combination where all conditions are on the same property and may be optimized
    """
    def __init__(self, prop, op, *children):
        self.prop = prop
        super(SinglePropCombination, self).__init__(op, *children)

    def as_query(self, prop_map):
        from temba.contacts.models import ContactField

        prop_obj = prop_map[self.prop]

        if isinstance(prop_obj, ContactField):
            # merge queries from children
            value_query = {}
            for child in self.children:
                value_query.update(**child.get_value_query())

            # TODO convert OR'd = conditions to IN etc

            return Q(id__in=Value.objects.filter(**value_query).values('contact_id'))
        else:
            return super(SinglePropCombination, self).as_query(prop_map)

    def __str__(self):
        op = 'OR' if self.op == operator.or_ else 'AND'
        return '%s[%s](%s)' % (op, self.prop, ', '.join([six.text_type(c) for c in self.children]))


# ================================== Parser definition ==================================

precedence = (
    ('left', 'OR'),
    ('left', 'AND'),
)


def p_expression_and(p):
    """expression : expression AND expression"""
    p[0] = BoolCombination(operator.and_, p[1], p[3])


def p_expression_or(p):
    """expression : expression OR expression"""
    p[0] = BoolCombination(operator.or_, p[1], p[3])


def p_expression_grouping(p):
    """expression : LPAREN expression RPAREN"""
    p[0] = p[2]


def p_expression_comparison(p):
    """expression : TEXT COMPARATOR literal"""
    p[0] = Condition(p[1].lower(), p[2].lower(), p[3])


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
    return ContactQuery(search_parser.parse(text, lexer=search_lexer))
