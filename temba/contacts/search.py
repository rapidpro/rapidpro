from __future__ import unicode_literals

import ply.lex as lex
import pytz
import re

from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from ply import yacc
from temba.utils import str_to_datetime
from temba.values.models import Value

# Originally based on this DSL for Django ORM: http://www.matthieuamiguet.ch/blog/my-djangocon-eu-slides-are-online
# Changed to produce querysets rather than Q queries, as Q queries that reference different objects can't be properly
# combined in AND expressions.

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


def contact_search(org, query, base_queryset):
    """
    Searches for contacts
    :param org: the org (used for date formats and timezones)
    :param query: the query, e.g. 'name = "Bob"'
    :param base_queryset: the base query set which queries operate on
    :return: a tuple of the contact query set, a boolean whether query was complex
    """
    from .models import ContactURN
    global PROPERTY_ALIASES
    if not PROPERTY_ALIASES:
        PROPERTY_ALIASES = {scheme: 'urns__path' for scheme, label in ContactURN.SCHEME_CHOICES}

    try:
        return contact_search_complex(org, query, base_queryset), True
    except SearchException:
        pass

    # if that didn't work, try again as simple name or urn path query
    return contact_search_simple(org, query, base_queryset), False


def contact_search_simple(org, query, base_queryset):
    """
    Performs a simple term based search, e.g. 'Bob' or '250783835665'
    """
    matches = ('name__icontains',) if org.is_anon else ('name__icontains', 'urns__path__icontains')
    terms = query.split()
    q = Q(pk__gt=0)

    for term in terms:
        term_query = Q(pk__lt=0)
        for match in matches:
            term_query |= Q(**{match: term})

        if org.is_anon:
            # try id match for anon orgs
            try:
                term_as_int = int(term)
                term_query |= Q(id=term_as_int)
            except ValueError:
                pass

        q &= term_query

    return base_queryset.filter(q).distinct()


def contact_search_complex(org, query, base_queryset):
    """
    Performs a complex query based search, e.g. 'name = "Bob" AND age > 18'
    """
    global search_lexer, search_parser

    # attach context to the lexer
    search_lexer.org = org
    search_lexer.base_queryset = base_queryset

    # combining results from multiple joins can lead to duplicates
    return search_parser.parse(query, lexer=search_lexer).distinct()


def generate_queryset(lexer, identifier, comparator, value):
    """
    Generates a queryset from the base and given field condition
    :param lexer: the lexer
    :param identifier: the contact attribute or field name, e.g. name
    :param comparator: the comparator, e.g. =
    :param value: the literal value, e.g. "Bob"
    :return: the query set
    """
    # resolve identifier aliases, e.g. '>' -> 'gt'
    if identifier in PROPERTY_ALIASES.keys():
        identifier = PROPERTY_ALIASES[identifier]

    if identifier in NON_FIELD_PROPERTIES:
        if identifier == 'urns__path' and lexer.org.is_anon:
            raise SearchException("Cannot search by URN in anonymous org")

        q = generate_non_field_comparison(identifier, comparator, value)
    else:
        from temba.contacts.models import ContactField
        try:
            field = ContactField.objects.get(org_id=lexer.org.id, key=identifier)
        except ObjectDoesNotExist:
            raise SearchException("Unrecognized contact field identifier %s" % identifier)

        if comparator.lower() in ('=', 'is') and value == "":
            q = generate_empty_field_test(field)
        elif field.value_type == Value.TYPE_TEXT:
            q = generate_text_field_comparison(field, comparator, value)
        elif field.value_type == Value.TYPE_DECIMAL:
            q = generate_decimal_field_comparison(field, comparator, value)
        elif field.value_type == Value.TYPE_DATETIME:
            q = generate_datetime_field_comparison(field, comparator, value, lexer.org)
        elif field.value_type in (Value.TYPE_STATE, Value.TYPE_DISTRICT, Value.TYPE_WARD):
            q = generate_location_field_comparison(field, comparator, value)
        else:  # pragma: no cover
            raise SearchException("Unrecognized contact field type '%s'" % field.value_type)

    return lexer.base_queryset.filter(q)


def generate_non_field_comparison(relation, comparator, value):
    lookup = TEXT_LOOKUP_ALIASES.get(comparator, None)
    if not lookup:
        raise SearchException("Unsupported comparator %s for non-field" % comparator)

    return Q(**{'%s__%s' % (relation, lookup): value})


def generate_empty_field_test(field):
    contacts_with_field = field.org.org_contacts.filter(Q(**{'values__contact_field__id': field.id}))
    return ~Q(**{'pk__in': contacts_with_field})


def generate_text_field_comparison(field, comparator, value):
    lookup = TEXT_LOOKUP_ALIASES.get(comparator, None)
    if not lookup:
        raise SearchException("Unsupported comparator %s for text field" % comparator)

    return Q(**{'values__contact_field__id': field.id, 'values__string_value__%s' % lookup: value})


def generate_decimal_field_comparison(field, comparator, value):
    lookup = DECIMAL_LOOKUP_ALIASES.get(comparator, None)
    if not lookup:
        raise SearchException("Unsupported comparator %s for decimal field" % comparator)

    try:
        value = Decimal(value)
    except Exception:
        raise SearchException("Can't convert '%s' to a decimal" % unicode(value))

    return Q(**{'values__contact_field__id': field.id, 'values__decimal_value__%s' % lookup: value})


def generate_datetime_field_comparison(field, comparator, value, org):
    lookup = DATETIME_LOOKUP_ALIASES.get(comparator, None)
    if not lookup:
        raise SearchException("Unsupported comparator %s for datetime field" % comparator)

    # parse as localized date and then convert to UTC
    tz = pytz.timezone(org.timezone)
    local_date = str_to_datetime(value, tz, org.get_dayfirst(), fill_time=False)

    # passed date wasn't parseable so don't match any contact
    if not local_date:
        return Q(pk=-1)

    value = local_date.astimezone(pytz.utc)

    if lookup == '<equal>':  # check if datetime is between date and date + 1d, i.e. anytime in that 24 hour period
        return Q(**{
            'values__contact_field__id': field.id,
            'values__datetime_value__gte': value,
            'values__datetime_value__lt': value + timedelta(days=1)})
    elif lookup == 'lte':  # check if datetime is less then date + 1d, i.e. that day and all previous
        return Q(**{
            'values__contact_field__id': field.id,
            'values__datetime_value__lt': value + timedelta(days=1)})
    elif lookup == 'gt':  # check if datetime is greater than or equal to date + 1d, i.e. day after and subsequent
        return Q(**{
            'values__contact_field__id': field.id,
            'values__datetime_value__gte': value + timedelta(days=1)})
    else:
        return Q(**{'values__contact_field__id': field.id, 'values__datetime_value__%s' % lookup: value})


def generate_location_field_comparison(field, comparator, value):
    lookup = LOCATION_LOOKUP_ALIASES.get(comparator, None)
    if not lookup:
        raise SearchException("Unsupported comparator %s for location field" % comparator)

    return Q(**{
        'values__contact_field__id': field.id,
        'values__location_value__name__%s' % lookup: value})


# ================================== Lexer definition ==================================

tokens = ('BINOP', 'COMPARATOR', 'TEXT', 'STRING')

literals = '()'

# treat reserved words specially
# http://www.dabeaz.com/ply/ply.html#ply_nn4
reserved = {
    'or': 'BINOP',
    'and': 'BINOP',
    'has': 'COMPARATOR',
    'is': 'COMPARATOR',
}

t_ignore = ' \t'  # ignore tabs and spaces


def t_COMPARATOR(t):
    r"""(?i)~|=|[<>]=?|~~?"""
    return t


def t_STRING(t):
    r"""("[^"]*")"""
    t.value = t.value[1:-1]
    return t


def t_TEXT(t):
    r"""[\w_\.\+\-\/]+"""
    t.type = reserved.get(t.value.lower(), 'TEXT')
    return t


def t_error(t):
    raise SearchException("Invalid character %s" % t.value[0])


# ================================== Parser definition ==================================

precedence = (
    (str('left'), str('BINOP')),
)


def p_expression_binop(p):
    """expression : expression BINOP expression"""
    if p[2].lower() == 'and':
        p[0] = p[1] & p[3]
    elif p[2].lower() == 'or':
        p[0] = p[1] | p[3]


def p_expression_grouping(p):
    """expression : '(' expression ')'"""
    p[0] = p[2]


def p_expression_comparison(p):
    """expression : TEXT COMPARATOR literal"""
    p[0] = generate_queryset(p.lexer, p[1].lower(), p[2].lower(), p[3])


def p_literal(p):
    """literal : TEXT
               | STRING"""
    p[0] = p[1]


def p_error(p):
    message = ("Syntax error at '%s'" % p.value) if p else "Syntax error"
    raise SearchException(message)


# ================================== Module initialization ==================================

# initalize the PLY library for lexing and parsing
search_lexer = lex.lex(reflags=re.UNICODE)
search_parser = yacc.yacc(write_tables=False)


def lexer_test(data):  # pragma: no cover
    """
    Convenience function for manual testing of lexer output
    """
    global search_lexer

    search_lexer.input(data)
    while True:
        tok = search_lexer.token()
        if not tok:
            break
        print tok
