# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import pytz
import regex
import six

from temba_expressions.evaluator import Evaluator, EvaluationContext, EvaluationStrategy, DEFAULT_FUNCTION_MANAGER
from temba.contacts.models import ContactField

ALLOWED_TOP_LEVELS = ('channel', 'contact', 'date', 'extra', 'flow', 'step', 'parent', 'child')

evaluator = Evaluator(allowed_top_levels=ALLOWED_TOP_LEVELS)

listing = None  # lazily initialized


def evaluate_template(template, context, url_encode=False, partial_vars=False):
    strategy = EvaluationStrategy.RESOLVE_AVAILABLE if partial_vars else EvaluationStrategy.COMPLETE
    return evaluator.evaluate_template(template, context, url_encode, strategy)


def evaluate_template_compat(template, context, url_encode=False):
    """
    Evaluates the given template which may contain old style expressions
    """
    template = migrate_template(template)
    return evaluator.evaluate_template(template, context, url_encode)


def get_function_listing():
    global listing

    if listing is None:
        listing = [{'name': f['name'], 'display': f['description'], 'signature': _build_function_signature(f)} for f in DEFAULT_FUNCTION_MANAGER.build_listing()]
    return listing


def _build_function_signature(f):
    signature = f['name'] + "("
    params_len = len(f['params'])

    formatted_params_list = []
    for param in f['params']:
        formatted_param = param['name']
        optional = param['optional']
        vararg = param['vararg']

        if optional and vararg:
            formatted_param = "[" + formatted_param + "], ..."

        elif optional:
            formatted_param = "[" + formatted_param + "]"

        elif vararg:
            formatted_param += ", ..."

        if len(formatted_params_list) < params_len - 1:
            formatted_param += ","
        formatted_params_list.append(formatted_param)

    return signature + " ".join(formatted_params_list) + ")"


# ======================================================================================================================
# Old style expression migration
# ======================================================================================================================

FILTER_REPLACEMENTS = {'lower_case': 'LOWER({0})',
                       'upper_case': 'UPPER({0})',
                       'capitalize': 'PROPER({0})',
                       'title_case': 'PROPER({0})',
                       'first_word': 'FIRST_WORD({0})',
                       'remove_first_word': 'REMOVE_FIRST_WORD({0})',
                       'read_digits': 'READ_DIGITS({0})',
                       'time_delta': '{0} + {1}'}


def migrate_template(text):
    """
    Migrates text which may contain filter style expressions or equals style expressions
    """
    migrated = text

    if '=' in migrated:
        migrated = replace_equals_style(migrated)
    if '@' in migrated and '|' in migrated:
        migrated = replace_filter_style(migrated)

    return migrated


def replace_filter_style(text):
    """
    Migrates text which may contain filter style expressions, e.g. "Hi @contact.name|upper_case", converting them to
    new style expressions, e.g. "Hi @(UPPER(contact))"
    """
    def replace_expression(match):
        expression = match.group(1)
        new_style = convert_filter_style(expression)
        if '|' in expression:
            new_style = '(%s)' % new_style  # add enclosing parentheses
        return '@' + new_style

    context_keys_joined_pattern = r'[\|\w]*|'.join(ALLOWED_TOP_LEVELS)
    pattern = r'\B@([\w]+[\.][\w\.\|]*[\w](:([\"\']).*?\3)?|' + context_keys_joined_pattern + r'[\|\w]*)'

    rexp = regex.compile(pattern, flags=regex.MULTILINE | regex.UNICODE | regex.V0)
    return rexp.sub(replace_expression, text)


def convert_filter_style(expression):
    """
    Converts a filter style expression, e.g. contact.name|upper_case, to new style, e.g. UPPER(contact)
    """
    if '|' not in expression:
        return expression

    components = expression.split('|')
    context_item = components[0]
    filters = components[1:]

    new_style = context_item
    for _filter in filters:
        if ':' in _filter:
            name, param = _filter.split(':')
            if param[0] == '"' or param[0] == "'":
                param = param[1:-1]  # strip quotes
        else:
            name, param = _filter, ''

        replacement = FILTER_REPLACEMENTS.get(name.lower(), None)
        if replacement:
            new_style = replacement.replace('{0}', new_style).replace('{1}', param)

    new_style = new_style.replace('+ -', '- ')  # collapse "+ -N" to "- N"

    return new_style


def replace_equals_style(text):
    """
    Migrates text which may contain equals style expressions, e.g. "Hi =UPPER(contact)", converting them to new style
    expressions, e.g. "Hi @(UPPER(contact))"
    """
    STATE_BODY = 0            # not in a expression
    STATE_PREFIX = 1          # '=' prefix that denotes the start of an expression
    STATE_IDENTIFIER = 2      # the identifier part, e.g. 'SUM' in '=SUM(1, 2)' or 'contact.age' in '=contact.age'
    STATE_BALANCED = 3        # the balanced parentheses delimited part, e.g. '(1, 2)' in 'SUM(1, 2)'
    STATE_STRING_LITERAL = 4  # a string literal
    input_chars = list(text)
    output_chars = []
    state = STATE_BODY
    current_expression_chars = []
    current_expression_terminated = False
    parentheses_level = 0

    def replace_expression(expression):
        expression_body = expression[1:]

        # if expression doesn't end with ) then check it's an allowed top level context reference
        if not expression_body.endswith(')'):
            top_level = expression_body.split('.')[0].lower()
            if top_level not in ALLOWED_TOP_LEVELS:
                return expression

        return '@' + convert_equals_style(expression_body)

    # determines whether the given character is a word character, i.e. \w in a regex
    def is_word_char(c):
        return c and (c.isalnum() or c == '_')

    for pos, ch in enumerate(input_chars):
        # in order to determine if the b in a.b terminates an identifier, we have to peek two characters ahead as it
        # could be a.b. (b terminates) or a.b.c (b doesn't terminate)
        next_ch = input_chars[pos + 1] if (pos < (len(input_chars) - 1)) else None
        next_next_ch = input_chars[pos + 2] if (pos < (len(input_chars) - 2)) else None

        if state == STATE_BODY:
            if ch == '=' and (is_word_char(next_ch) or next_ch == '('):
                state = STATE_PREFIX
                current_expression_chars = [ch]
            else:
                output_chars.append(ch)

        elif state == STATE_PREFIX:
            if is_word_char(ch):
                # we're parsing an expression like =XXX or =YYY()
                state = STATE_IDENTIFIER
            elif ch == '(':
                # we're parsing an expression like =(1 + 2)
                state = STATE_BALANCED
                parentheses_level += 1

            current_expression_chars.append(ch)

        elif state == STATE_IDENTIFIER:
            if ch == '(':
                state = STATE_BALANCED
                parentheses_level += 1

            current_expression_chars.append(ch)

        elif state == STATE_BALANCED:
            if ch == '(':
                parentheses_level += 1
            elif ch == ')':
                parentheses_level -= 1
            elif ch == '"':
                state = STATE_STRING_LITERAL

            current_expression_chars.append(ch)

            # expression terminates if parentheses balance
            if parentheses_level == 0:
                current_expression_terminated = True

        elif state == STATE_STRING_LITERAL:
            if ch == '"':
                state = STATE_BALANCED
            current_expression_chars.append(ch)

        # identifier can terminate expression in 3 ways:
        #  1. next char is null (i.e. end of the input)
        #  2. next char is not a word character or period or left parentheses
        #  3. next char is a period, but it's not followed by a word character
        if state == STATE_IDENTIFIER:
            if not next_ch \
                    or (not is_word_char(next_ch) and not next_ch == '.' and not next_ch == '(') \
                    or (next_ch == '.' and not is_word_char(next_next_ch)):
                current_expression_terminated = True

        if current_expression_terminated:
            output_chars.append(replace_expression(''.join(current_expression_chars)))
            current_expression_chars = []
            current_expression_terminated = False
            state = STATE_BODY

    return ''.join(output_chars)


def convert_equals_style(expression):
    """
    Converts a equals style expression, e.g. UPPER(contact), to new style, e.g. (UPPER(contact))
    """
    if '(' not in expression:  # e.g. contact or contact.name
        return expression

    # some users have been putting @ expressions inside = expressions which works due to the old two pass nature of
    # expression evaluation
    def replace_embedded_filter_style(match):
        filter_style = match.group(2)
        return convert_filter_style(filter_style)

    pattern = r'(")?@((%s)[\.\w\|]*)(\1)?' % '|'.join(ALLOWED_TOP_LEVELS)

    rexp = regex.compile(pattern, flags=regex.MULTILINE | regex.UNICODE | regex.V0)
    expression = rexp.sub(replace_embedded_filter_style, expression)

    if not expression.startswith('('):
        expression = '(%s)' % expression

    return expression


class ContactFieldCollector(EvaluationContext):
    """
    A simple evaluator that extracts contact fields from the parse tree
    """

    @classmethod
    def get_contact_field(cls, path):
        parts = path.split('.')
        if len(parts) > 1:
            if parts[0] in ('parent', 'child'):
                parts = parts[1:]
                if len(parts) < 2:
                    return None
            if parts[0] == 'contact':
                field_name = parts[1]
                if ContactField.is_valid_key(field_name):
                    return parts[1]
        return None

    def __init__(self):
        super(ContactFieldCollector, self).__init__(dict(), pytz.UTC, None)

    def get_contact_fields(self, msg):
        self.contact_fields = set()
        if msg:
            evaluate_template(six.text_type(msg), self, False, True)
        return self.contact_fields

    def resolve_variable(self, path):
        contact_field = ContactFieldCollector.get_contact_field(path)
        if contact_field:
            self.contact_fields.add(contact_field)
        return ""
