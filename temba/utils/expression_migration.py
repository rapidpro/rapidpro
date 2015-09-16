from __future__ import absolute_import, unicode_literals

import regex


FILTER_REPLACEMENTS = {'lower_case': 'LOWER({0})',
                       'upper_case': 'UPPER({0})',
                       'capitalize': 'PROPER({0})',
                       'title_case': 'PROPER({0})',
                       'first_word': 'FIRST_WORD({0})',
                       'remove_first_word': 'REMOVE_FIRST_WORD({0})',
                       'read_digits': 'READ_DIGITS({0})',
                       'time_delta': '({0} + {1})'}


def migrate_flow_definition(json_flow):

    def process_object(item):
        for key, val in item.iteritems():
            if isinstance(val, basestring):
                if '@' in val or '=' in val:
                    item[key] = migrate_substitutable_text(val)
            if isinstance(val, list):
                for n in range(len(val)):
                    if '@' in val[n] or '=' in val[n]:
                        val[n] = migrate_substitutable_text(val[n])
            if isinstance(val, dict):
                process_object(val)

    for rule_set in json_flow['rule_sets']:
        for rule in rule_set['rules']:
            process_object(rule['test'])

        rule_set['operand'] = migrate_substitutable_text(rule_set['operand'])
        if 'webhook' in rule_set and rule_set['webhook']:
            rule_set['webhook'] = migrate_substitutable_text(rule_set['webhook'])

    for action_set in json_flow['action_sets']:
        for action in action_set['actions']:
            process_object(action)


def migrate_substitutable_text(text):
    """
    Migrates text which may contain filter style expressions or equals style expressions
    """
    print 'Processing substitutable text: %s' % text
    migrated = text

    if '@' in migrated and '|' in migrated:
        migrated = replace_filter_style(migrated)
    if '=' in migrated:
        migrated = replace_equals_style(migrated)

    if migrated != text:
        print ' > Migrated to: %s' % migrated

    return migrated


def replace_filter_style(text):
    """
    Migrates text which may contain filter style expressions, e.g. "Hi @contact.name|upper_case", converting them to
    new style expressions, e.g. "Hi @(UPPER(contact))"
    """
    def replace_expression(match):
        expression = match.group(1)
        return '@' + convert_filter_style(expression)

    context_keys_joined_pattern = r'[\|\w]*|'.join(['step', 'contact', 'flow', 'extra', 'channel', 'date'])
    pattern = r'\B@([\w]+[\.][\w\.\|]*[\w](:([\"\']).*?\3)?|' + context_keys_joined_pattern + r'[\|\w]*)'

    rexp = regex.compile(pattern, flags=regex.MULTILINE | regex.UNICODE | regex.V0)
    return rexp.sub(replace_expression, text)


def convert_filter_style(expression):
    """
    Converts a filter style expression, e.g. contact.name|upper_case, to new style, e.g. (UPPER(contact))
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
            if param[0] == '"':
                param = param[1:-1]  # strip quotes
        else:
            name, param = _filter, ''

        replacement = FILTER_REPLACEMENTS.get(name.lower(), None)
        if replacement:
            new_style = replacement.replace('{0}', new_style).replace('{1}', param)

    new_style = '(%s)' % new_style  # add enclosing parentheses

    print " > Converted filter style expression: %s -> %s" % (expression, new_style)
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

    # determines whether the given character is a word character, i.e. \w in a regex
    is_word_char = lambda c: c and (c.isalnum() or c == '_')

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
            output_chars.append(convert_equals_style(''.join(current_expression_chars)))
            current_expression_chars = []
            current_expression_terminated = False
            state = STATE_BODY

    return ''.join(output_chars)


def convert_equals_style(expression):
    """
    Converts a equals style expression, e.g. =UPPER(contact), to new style, e.g. @(UPPER(contact))
    """
    expression_body = expression[1:]
    if not expression_body.startswith('('):
        expression_body = '(%s)' % expression_body
    return '@' + expression_body


#
# TESTING...
#
def test():
    from temba.msgs.models import Broadcast
    from temba.flows.models import FlowVersion, CURRENT_EXPORT_VERSION

    print "Flow definitions..."

    for flow_version in FlowVersion.objects.filter(version_number=CURRENT_EXPORT_VERSION):
        json_flow = flow_version.get_definition_json()
        migrate_flow_definition(json_flow)

    print "Scheduled broadcasts..."

    for broadcast in Broadcast.objects.exclude(schedule=None):
        if '@' in broadcast.text or '=' in broadcast.text:
            migrate_substitutable_text(broadcast.text)
