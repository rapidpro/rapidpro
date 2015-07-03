from __future__ import unicode_literals

import inspect
import logging
import math
import json
import ply.lex as lex
import ply.yacc as yacc
import regex
from datetime import timedelta, date, datetime, time
from decimal import Decimal, DivisionByZero
from django.utils.http import urlquote
from django.utils.translation import ugettext_lazy as _
from . import datetime_to_str, get_datetime_format, format_decimal, str_to_datetime, str_to_time


logger = logging.getLogger(__name__)


class EvaluationError(Exception):
    """
    Exception class for errors during template/expression evaluation
    """
    def __init__(self, message, caused_by=None):
        Exception.__init__(self, message)
        self.caused_by = caused_by


class EvaluationContext(object):
    """
    Evaluation context, i.e. variables and date options
    """
    def __init__(self, variables, date_options):
        self.variables = dict(true=True, false=False)  # built-in variables
        self.variables.update(variables)
        self.date_options = date_options

    def get_date_format(self, inc_time):
        formats = get_datetime_format(self.date_options['dayfirst'])
        return formats[1] if inc_time else formats[0]

    def keys(self):
        return self.variables.keys()


def evaluate_template(template, context, url_encode=False):
    """
    For now we have to support both the old and new style of templates. We chain the methods for both styles and combine
    the error lists to get the final evaluated output
    """
    evaluated, errors1 = evaluate_template_old(template, context, url_encode)
    evaluated, errors2 = evaluate_template_new(evaluated, context, url_encode)
    return evaluated, errors1 + errors2


def evaluate_template_old(template, context, url_encode=False):
    """
    Evaluates an old style template string, e.g. "Hello @contact.name|upper_case you have @contact.reports reports"
    :param template: the template string
    :param context: the evaluation context
    :param url_encode: whether or not values should be URL encoded
    :return: a tuple of the evaluated string and a list of evaluation errors
    """
    errors = []

    def resolve_expression(match):
        expression = match.group(1)

        try:
            evaluated = evaluate_expression_old(expression, context)

            if url_encode:
                evaluated = urlquote(evaluated)
        except EvaluationError, e:
            logger.debug("EvaluationError: %s" % e.message)

            # if we can't evaluate expression, include it as is in the output
            errors.append(e.message)
            return match.group(0)

        return evaluated

    # build pattern that match the context keys even if they have filters and do not match twitter handle and
    context_keys_joined_pattern = r'[\|\w]*|'.join(context.keys())
    pattern = r'\B@([\w]+[\.][\w\.\|]*[\w](:([\"\']).*?\3)?|' + context_keys_joined_pattern + r'[\|\w]*)'

    # substitute classic style @xxx.yyy[|filter[:"param"]] expressions
    rexp = regex.compile(pattern, flags=regex.MULTILINE | regex.UNICODE)
    return rexp.sub(resolve_expression, template), errors


def evaluate_expression_old(expression, context):
    """
    Evaluates an old style (variable + filters) expression, e.g. "contact.name|upper_case"
    :param expression: the expression
    :param context: the evaluation context
    :return: the evaluated value as a string
    """
    # empty string case
    if not expression:
        return ''

    # split off our filters
    parts = expression.split('|')

    unfiltered = parts[0].strip()
    filters = parts[1:]

    # update evaluation globals
    set_evaluation_context(context)

    try:
        value = evaluate_variable(unfiltered, context.variables)
    except EvaluationError, e:
        # evaluate_variable is executed recursively so we catch exception below so we can report full identifier name
        raise EvaluationError("Undefined variable '%s'" % unfiltered, e)

    # convert value to string before applying filters
    result = val_to_string(value)

    # apply our filters in order
    for filter_key in filters:
        result = apply_filter_old(result, filter_key, context.date_options['tz'], context.date_options['dayfirst'])

    return result


def apply_filter_old(value, filter_key, tz, dayfirst):
    """
    Applies an old style filter to a value
    :param value: the value
    :param filter_key: the filter key, e.g. 'lower_case'
    :param tz: the timezone to use for date operations
    :return: the filtered value
    """
    def chunk(str, chunk_size):
        return [str[i:i+chunk_size] for i in range(0, len(str), chunk_size)]

    if not value:
        return value

    if filter_key == 'lower_case':
        return value.lower()
    elif filter_key == 'upper_case':
        return value.upper()
    elif filter_key == 'capitalize':
        return value.capitalize()
    elif filter_key == 'title_case':
        return value.title()
    elif filter_key == 'first_word':
        words = regex.split(r"[\W]+", value.strip(), flags=regex.UNICODE)
        if words:
            return words[0]
        else:
            return value
    elif filter_key == 'remove_first_word':
        words = regex.split(r"([\W]+)", value.strip(), flags=regex.UNICODE)
        if len(words) > 2:
            return "".join(words[2:])
        else:
            return ''
    elif filter_key == 'read_digits':

        # trim off the plus for phone numbers
        if value and value[0] == '+':
            value = value[1:]

        length = len(value)

        # ssn
        if length == 9:
            result = ' '.join(value[:3])
            result += ' , ' + ' '.join(value[3:5])
            result += ' , ' + ' '.join(value[5:])
            return result

        # triplets, most international phone numbers
        if length % 3 == 0 and length > 3:
            chunks = chunk(value, 3)
            return ' '.join(','.join(chunks))

        # quads, credit cards
        if length % 4 == 0:
            chunks = chunk(value, 4)
            return ' '.join(','.join(chunks))

        # otherwise, just put a comma between each number
        return ','.join(value)

    elif filter_key[:10] == 'time_delta':
        filter_arg = filter_key[12:-1]
        input_date = str_to_datetime(value, tz, dayfirst)
        if input_date is not None:
            time_diff = int(filter_arg)
            if time_diff:
                result = input_date + timedelta(days=time_diff)
            else:
                result = input_date

            # format as localized again for result
            formats = get_datetime_format(dayfirst)
            return datetime_to_str(result, formats[1], False, tz)
        else:
            return ''
    else:
        return value


# Temba templates support 3 forms of embedded expression:
#  1. Single variable, e.g. =contact, =contact.name (delimited by character type or end of input)
#  2. Single function, e.g. =SUM(1, 2) (delimited by balanced parentheses)
#  3. Contained expression, e.g. =(SUM(1, 2) + 2) (delimited by balanced parentheses)
STATE_BODY = 0            # not in a expression
STATE_PREFIX = 1          # '=' prefix that denotes the start of an expression
STATE_IDENTIFIER = 2      # the identifier part, e.g. 'SUM' in '=SUM(1, 2)' or 'contact.age' in '=contact.age'
STATE_BALANCED = 3        # the balanced parentheses delimited part, e.g. '(1, 2)' in 'SUM(1, 2)'
STATE_STRING_LITERAL = 4  # a string literal


def evaluate_template_new(template, context, url_encode=False):
    """
    Evaluates a new style template string, e.g. "Hello =contact.name you have =(contact.reports * 2) reports"
    :param template: the template string
    :param context: the evaluation context
    :param url_encode: whether or not values should be URL encoded
    :return: a tuple of the evaluated template and a list of evaluation errors
    """
    input_chars = list(template)
    output_chars = []
    errors = []
    state = STATE_BODY
    current_expression_chars = []
    current_expression_terminated = False
    parentheses_level = 0

    # determines whether the given character is a word character, i.e. \w in a regex
    is_word_char = lambda c: c and (c.isalnum() or c == '_')

    def resolve_expression(expression):
        """
        Resolves an expression found in the template. If an evaluation error occurs, expression is returned as is.
        """
        try:
            evaluated = evaluate_expression(expression[1:], context)  # remove prefix and evaluate

            # convert result to string
            result = val_to_string(evaluated)

            return urlquote(result) if url_encode else result
        except EvaluationError, e:
            logger.debug("EvaluationError: %s" % e.message)

            # if we can't evaluate expression, include it as is in the output
            errors.append(e.message)
            return expression

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
            output_chars.append(resolve_expression(''.join(current_expression_chars)))
            current_expression_chars = []
            current_expression_terminated = False
            state = STATE_BODY

    output = ''.join(output_chars)  # joining is fastest way to build strings in Python
    return output, errors


def evaluate_expression(expression, context):
    """
    Evaluates an expression, e.g. "contact.name" or "contact.reports * 2"
    :param expression: the expression string
    :param context: the evaluation context
    :return: the evaluated expression
    """
    global expression_lexer, expression_parser

    # empty string case
    if not expression:
        return ''

    set_evaluation_context(context)

    return expression_parser.parse(expression, lexer=expression_lexer)


def evaluate_variable(identifier, container):
    """
    Evaluates a single variable
    :param identifier: the variable name
    :param container: the container
    :return: the variable value or none
    """
    if '.' in identifier:
        (next_key, rest) = identifier.split('.', 1)
    else:
        next_key = identifier
        rest = None

    if isinstance(container, dict):
        value = container.get(next_key, None)
    else:
        value = getattr(container, next_key, None)

    if rest and value:
        return evaluate_variable(rest, value)
    elif value is not None:
        if isinstance(value, dict):
            if '__default__' in value:
                return value['__default__']
            else:
                try:
                    return json.dumps(value)
                except Exception:
                    return unicode(value)
        return value
    else:
        raise EvaluationError("No item called '%s' in container '%s'" % (identifier, unicode(container)))


def invoke_function(name, func, arguments):
    """
    Invokes the given function
    :param name: the name to use for error reporting (not the actual function name)
    :param func: the function object
    :param arguments: the passed arguments
    :return: the function result
    """
    args, varargs, keywords, defaults = inspect.getargspec(func)

    # build a mapping from argument names to their default values, if any:
    if defaults is None:
        defaults = {}
    else:
        defaulted_args = args[-len(defaults):]
        defaults = {name: val for name, val in zip(defaulted_args, defaults)}

    call_args = []
    passed_args = list(arguments)

    try:
        for arg in args:
            if passed_args:
                call_args.append(passed_args.pop(0))
            elif arg in defaults:
                call_args.append(defaults[arg])
            else:
                raise TypeError("Missing argument in call to %s(): %s" % (func.__name__, arg))

        if varargs is not None:
            call_args.extend(passed_args)
            passed_args = []

        # any unused arguments?
        if passed_args:
            raise TypeError("Function %s cannot be called with %d arguments" % (func.__name__, len(arguments)))

        return func(*call_args)
    except Exception, e:
        pretty_args = []
        for arg in arguments:
            pretty_args.append(('"%s"' % arg) if isinstance(arg, basestring) else unicode(arg))
        pretty_args = ', '.join(pretty_args)
        raise EvaluationError("Error calling function %s with arguments %s" % (name, pretty_args), e)


def get_evaluation_context():
    global current_evaluation_context
    return current_evaluation_context


def set_evaluation_context(context):
    global current_evaluation_context
    current_evaluation_context = context

#################################### Value conversion ####################################


def val_to_string(value):
    """
    Converts any value to a string
    """
    context = get_evaluation_context()

    if isinstance(value, date):
        tz = context.date_options['tz']
        format_str = context.get_date_format(inc_time=isinstance(value, datetime))
        return datetime_to_str(value, format_str, False, tz)
    elif isinstance(value, Decimal):
        return format_decimal(value)
    else:
        return unicode(value)


def val_to_date(value):
    """
    Attempts fuzzy conversion of any value to a date
    """
    context = get_evaluation_context()
    tz, dayfirst = context.date_options['tz'], context.date_options['dayfirst']

    if isinstance(value, datetime):
        return value.date()  # discard time
    elif isinstance(value, date):
        return value
    elif isinstance(value, basestring):
        parsed = str_to_datetime(value, tz, dayfirst, fill_time=False)
        if parsed is not None:
            return parsed.date()

    raise EvaluationError("Can't convert '%s' to a date" % unicode(value))


def val_to_datetime(value):
    """
    Attempts fuzzy conversion of any value to a datetime
    """
    context = get_evaluation_context()
    tz, dayfirst = context.date_options['tz'], context.date_options['dayfirst']

    if isinstance(value, datetime):
        return value
    elif isinstance(value, date):
        return datetime.combine(date, time(0, 0, 0, 0, tz))  # add 00:00 UTC time
    elif isinstance(value, basestring):
        parsed = str_to_datetime(value, tz, dayfirst, fill_time=False)
        if parsed is not None:
            return parsed

    raise EvaluationError("Can't convert '%s' to a datetime" % unicode(value))


def val_to_date_or_datetime(value):
    """
    Attempts fuzzy conversion of any value to a date or datetime, depending on what information it contains
    """
    context = get_evaluation_context()
    tz, dayfirst = context.date_options['tz'], context.date_options['dayfirst']

    if isinstance(value, date):  # return both dates and datetimes as is
        return value
    elif isinstance(value, basestring):
        parsed = str_to_datetime(value, tz, dayfirst, fill_time=False)
        if parsed is not None:
            if not parsed.hour and not parsed.minute and not parsed.second and not parsed.microsecond:
                return parsed.date()
            else:
                return parsed

    raise EvaluationError("Can't convert '%s' to a date or datetime" % unicode(value))


def val_to_time(value):
    """
    Attempts fuzzy conversion of any value to a time
    """
    if isinstance(value, time):
        return value
    elif isinstance(value, basestring):
        parsed = str_to_time(value)
        if parsed is not None:
            return parsed

    raise EvaluationError("Can't convert '%s' to a time" % unicode(value))


def val_to_integer(value):
    """
    Attempts conversion of any value to an integer
    """
    try:
        return int(value)
    except Exception, e:
        raise EvaluationError("Can't convert '%s' to an integer" % unicode(value), e)


def val_to_decimal(value):
    """
    Attempts conversion of any value to a Decimal
    """
    try:
        return Decimal(value)
    except Exception, e:
        raise EvaluationError("Can't convert '%s' to a decimal" % unicode(value), e)


def val_to_boolean(value):
    """
    Attempts conversion of any value to a boolean (called a 'Logical Value' in Excel lingo)
    """
    if isinstance(value, bool):
        return value
    elif isinstance(value, basestring):
        if value.lower() == 'true':
            return True
        elif value.lower() == 'false':
            return False

        # try converting to a decimal which differs from Excel behaviour but we are very flexible with types
        try:
            as_decimal = Decimal(value)
            return bool(as_decimal)
        except Exception:
            pass

        raise EvaluationError("Can't convert '%s' to a logical value" % unicode(value))
    else:
        return bool(value)


def vals_auto_convert(arg1, arg2):
    """
    Converts a pair of arguments to their most-likely types. This deviates from Excel which doesn't auto convert values
    but is necessary for us to intuitively handle contact fields which don't use the correct value type
    """
    try:
        # try parsing as two dates
        return val_to_date_or_datetime(arg1), val_to_date_or_datetime(arg2)
    except EvaluationError:
        pass

    try:
        # try parsing as two decimals
        return val_to_decimal(arg1), val_to_decimal(arg2)
    except EvaluationError:
        pass

    return arg1, arg2


#################################### Lexer definition ####################################

tokens = ('COMMA', 'LPAREN', 'RPAREN',
          'PLUS', 'MINUS', 'TIMES', 'DIVIDE', 'EXPONENT',
          'EQ', 'NEQ', 'GTE', 'GT', 'LTE', 'LT',
          'AMPERSAND',
          'NAME', 'STRING', 'DECIMAL', )

# Tokens
t_COMMA = r','
t_LPAREN = r'\('
t_RPAREN = r'\)'

t_PLUS = r'\+'
t_MINUS = r'-'
t_TIMES = r'\*'
t_DIVIDE = r'/'
t_EXPONENT = r'\^'

t_EQ = r'='
t_NEQ = r'<>'
t_GTE = r'>='
t_GT = r'>'
t_LTE = r'<='
t_LT = r'<'

t_AMPERSAND = r'&'
t_NAME = r'[a-zA-Z_][\w\.]*'  # variable names, e.g. contact.name or function names, e.g. SUM

# Ignored characters
t_ignore = " \t\n\r"


def t_STRING(t):
    r""""(""|[^"])*\""""
    t.value = t.value[1:-1]  # strip surrounding quotes
    t.value = t.value.replace('""', '"')  # unescape embedded quotes
    return t


def t_DECIMAL(t):
    r"""\d+(\.\d+)?"""
    t.value = Decimal(t.value)
    return t


def t_error(t):
    raise EvaluationError("Illegal character '%s'" % t.value[0])


#################################### Parser definition ####################################

# Precedence rules for the arithmetic operators (using str(...) because ply doesn't accept unicode strings)
precedence = (
    (str('nonassoc'), str('EQ'), str('NEQ'), str('GTE'), str('GT'), str('LTE'), str('LT')),
    (str('left'), str('AMPERSAND')),
    (str('left'), str('PLUS'), str('MINUS')),
    (str('left'), str('TIMES'), str('DIVIDE')),
    (str('left'), str('EXPONENT')),
    (str('right'), str('UMINUS'))
)


def p_statement_expr(p):
    """statement : expression"""
    p[0] = p[1]


def p_expression_function_call(p):
    """
    expression : NAME LPAREN parameters RPAREN
               | NAME LPAREN RPAREN
    """
    name, args = p[1], p[3] if len(p) == 5 else []

    try:
        func = expression_functions[name.lower()]
    except KeyError, e:
        raise EvaluationError("Undefined function '%s'" % name, e)

    p[0] = invoke_function(name, func, args)


def p_parameters_list(p):
    """
    parameters : expression
               | parameters COMMA expression
    """
    if len(p) == 2:
        p[0] = [p[1]]
    else:
        p[0] = p[1]
        p[0].append(p[3])


def p_expression_uminus(p):
    """expression : MINUS expression %prec UMINUS"""
    p[0] = - val_to_decimal(p[2])


def p_expression_exponent(p):
    """expression : expression EXPONENT expression"""
    p[0] = val_to_decimal(math.pow(val_to_decimal(p[1]), val_to_decimal(p[3])))


def p_expression_add_sub(p):
    """expression : expression PLUS expression
                  | expression MINUS expression"""
    arg1, op, arg2 = p[1], p[2], p[3]

    if isinstance(arg1, Decimal) and isinstance(arg2, Decimal):
        pass
    elif isinstance(arg2, time):
        arg1 = val_to_date_or_datetime(arg1)
        arg2 = timedelta(hours=arg2.hour, minutes=arg2.minute, seconds=arg2.second)
    else:
        # try parsing arg1 as a date and arg2 as an integer to determine if this should be a date + days operation
        try:
            arg1_as_date = val_to_date_or_datetime(arg1)
            arg2_as_integer = val_to_integer(arg2)
        except EvaluationError:
            arg1_as_date = None
            arg2_as_integer = None
            pass

        if arg1_as_date and arg2_as_integer:
            arg1 = arg1_as_date
            arg2 = timedelta(days=arg2_as_integer)
        else:
            # otherwise, both args must be decimals or parseable to decimals
            arg1 = val_to_decimal(arg1)
            arg2 = val_to_decimal(arg2)

    p[0] = arg1 + arg2 if op == '+' else arg1 - arg2


def p_expression_mul_div(p):
    """expression : expression TIMES expression
                  | expression DIVIDE expression"""
    arg1, op, arg2 = p[1], p[2], p[3]

    # both args must be decimals or parseable to decimals
    arg1 = val_to_decimal(arg1)
    arg2 = val_to_decimal(arg2)

    try:
        p[0] = arg1 * arg2 if op == '*' else arg1 / arg2
    except DivisionByZero, e:
        raise EvaluationError("Division by zero", e)


def p_expression_group(p):
    """expression : LPAREN expression RPAREN"""
    p[0] = p[2]


def p_expression_concatenation(p):
    """expression : expression AMPERSAND expression"""
    p[0] = val_to_string(p[1]) + val_to_string(p[3])


def p_expression_equality(p):
    """expression : expression EQ expression
                  | expression NEQ expression"""

    def vals_equal(obj1, obj2):
        # string equality is case-insensitive
        if isinstance(obj1, basestring) and isinstance(obj2, basestring):
            return obj1.lower() == obj2.lower()
        else:
            return obj1 == obj2

    arg1, op, arg2 = p[1], p[2], p[3]

    arg1, arg2 = vals_auto_convert(arg1, arg2)

    if op == '=':
        p[0] = vals_equal(arg1, arg2)
    elif op == '<>':
        p[0] = not vals_equal(arg1, arg2)


def p_expression_comparison(p):
    """expression : expression GTE expression
                  | expression GT expression
                  | expression LTE expression
                  | expression LT expression"""

    arg1, op, arg2 = p[1], p[2], p[3]

    arg1, arg2 = vals_auto_convert(arg1, arg2)

    if op == '>=':
        p[0] = (arg1 >= arg2)
    elif op == '>':
        p[0] = (arg1 > arg2)
    elif op == '<=':
        p[0] = (arg1 <= arg2)
    elif op == '<':
        p[0] = (arg1 < arg2)


def p_expression_string(p):
    """expression : STRING"""
    p[0] = p[1]


def p_expression_decimal(p):
    """expression : DECIMAL"""
    p[0] = p[1]


def p_expression_variable(p):
    """expression : NAME"""
    context = get_evaluation_context()

    try:
        p[0] = evaluate_variable(p[1].lower(), context.variables)
    except EvaluationError, e:
        # evaluate_variable is executed recursively so we catch exception below so we can report full name
        raise EvaluationError("Undefined variable '%s'" % p[1], e)


def p_error(p):
    message = ("Syntax error at '%s'" % p.value) if p else "Syntax error"
    raise EvaluationError(message)


#################################### Module initialization ####################################

# initalize the PLY library for lexing and parsing
expression_lexer = lex.lex()
expression_parser = yacc.yacc(write_tables=False)
current_evaluation_context = None  # global as PLY doesn't have a good way to pass this into its production functions

# gets all functions which will accessible from within expressions - i.e. those defined in the specific functions module
# and have the prefix f_. Organizes them by name with the prefix removed
import temba.utils.parser_functions as parser_functions
fn_module = parser_functions
expression_functions = {fn.__name__[2:]: fn for fn in fn_module.__dict__.copy().itervalues()
                        if inspect.isfunction(fn) and inspect.getmodule(fn) == fn_module and fn.__name__.startswith('f_')}


def get_function_listing():
    """
    This code will eventually make it into a view to provide auto-completion of functions
    """
    listing = []
    for fn_name in sorted(expression_functions.keys()):
        fn = expression_functions[fn_name]
        arg_spec = inspect.getargspec(fn)

        args = arg_spec.args
        if arg_spec.varargs:
            args.append(arg_spec.varargs)

        # enclose argument names with default values with [] to show that they're optional
        num_defaults = len(arg_spec.defaults) if arg_spec.defaults else 0
        for i, arg in enumerate(args):
            if i >= len(args) - num_defaults:
                args[i] = '[%s]' % arg

        signature = '%s(%s)' % (fn_name.upper(), ', '.join(args))
        description = _(fn.__doc__.strip())
        listing.append(dict(signature=signature, description=description))

    return listing
