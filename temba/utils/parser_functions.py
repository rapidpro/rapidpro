from __future__ import unicode_literals

import math
import operator
import regex

from datetime import date as _date, time as _time
from dateutil.relativedelta import relativedelta
from decimal import Decimal
from django.utils import timezone
from .parser import get_evaluation_context, val_to_string, val_to_date, val_to_datetime, val_to_date_or_datetime, \
    val_to_decimal, val_to_integer, val_to_time, val_to_boolean

#################################### Text ####################################


def f_char(number):
    """
    Returns the character specified by a number
    """
    return chr(val_to_integer(number))


def f_clean(text):
    """
    Removes all non-printable characters from a text string
    """
    text = val_to_string(text)
    return ''.join([c for c in text if ord(c) >= 32])


def f_code(text):
    """
    Returns a numeric code for the first character in a text string
    """
    return f_unicode(text)  # everything is unicode


def f_concatenate(*args):
    """
    Joins text strings into one text string
    """
    result = ''
    for arg in args:
        result += val_to_string(arg)
    return result


def f_fixed(number, decimals=2, no_commas=False):
    """
    Formats the given number in decimal format using a period and commas
    """
    number = val_to_decimal(number)
    if decimals < 0:
        number = round(number, decimals)
        decimals = 0

    format_str = '{:.%df}' % decimals if no_commas else '{:,.%df}' % decimals
    return format_str.format(number)


def f_left(text, num_chars):
    """
    Returns the first characters in a text string
    """
    if num_chars < 0:
        raise ValueError("Number of chars can't be negative")
    return val_to_string(text)[0:val_to_integer(num_chars)]


def f_len(text):
    """
    Returns the number of characters in a text string
    """
    return len(val_to_string(text))


def f_lower(text):
    """
    Converts a text string to lowercase
    """
    return val_to_string(text).lower()


def f_proper(text):
    """
    Capitalizes the first letter of every word in a text string
    """
    return val_to_string(text).title()


def f_rept(text, number_times):
    """
    Repeats text a given number of times
    """
    if number_times < 0:
        raise ValueError("Number of times can't be negative")
    return val_to_string(text) * val_to_integer(number_times)


def f_right(text, num_chars):
    """
    Returns the last characters in a text string
    """
    if num_chars < 0:
        raise ValueError("Number of chars can't be negative")
    return val_to_string(text)[-val_to_integer(num_chars):]


def f_substitute(text, old_text, new_text, instance_num=-1):
    """
    Substitutes new_text for old_text in a text string
    """
    text = val_to_string(text)
    old_text = val_to_string(old_text)
    new_text = val_to_string(new_text)

    if instance_num < 0:
        return text.replace(old_text, new_text)
    else:
        splits = text.split(old_text)
        output = splits[0]
        instance = 1
        for split in splits[1:]:
            sep = new_text if instance == instance_num else old_text
            output += sep + split
            instance += 1
        return output


def f_unichar(number):
    """
    Returns the unicode character specified by a number
    """
    return unichr(val_to_integer(number))


def f_unicode(text):
    """
    Returns a numeric code for the first character in a text string
    """
    text = val_to_string(text)
    if len(text) == 0:
        raise ValueError("Text can't be empty")
    return ord(text[0])


def f_upper(text):
    """
    Converts a text string to uppercase
    """
    return val_to_string(text).upper()


#################################### Date and time ####################################


def f_date(year, month, day):
    """
    Defines a date value
    """
    return _date(val_to_integer(year), val_to_integer(month), val_to_integer(day))


def f_datevalue(text):
    """
    Converts date stored in text to an actual date
    """
    return val_to_date(text)


def f_day(date):
    """
    Returns only the day of the month of a date (1 to 31)
    """
    return val_to_date_or_datetime(date).day


def f_edate(date, months):
    """
    Moves a date by the given number of months
    """
    return val_to_date_or_datetime(date) + relativedelta(months=val_to_integer(months))


def f_hour(datetime):
    """
    Returns only the hour of a datetime (0 to 23)
    """
    return val_to_datetime(datetime).hour


def f_minute(datetime):
    """
    Returns only the minute of a datetime (0 to 59)
    """
    return val_to_datetime(datetime).minute


def f_month(date):
    """
    Returns only the month of a date (1 to 12)
    """
    return val_to_date_or_datetime(date).month


def f_now():
    """
    Returns the current date and time
    """
    # for consistency, take datetime from the context if it's defined
    variables = get_evaluation_context().variables
    date_variables = variables.get('date', None)
    return val_to_datetime(date_variables['now']) if date_variables else timezone.now()


def f_second(datetime):
    """
    Returns only the second of a datetime (0 to 59)
    """
    return val_to_datetime(datetime).second


def f_time(hours, minutes, seconds):
    """
    Defines a time value
    """
    return _time(val_to_integer(hours), val_to_integer(minutes), val_to_integer(seconds))


def f_timevalue(text):
    """
    Converts time stored in text to an actual time
    """
    return val_to_time(text)


def f_today():
    """
    Returns the current date
    """
    # for consistency, take date from the context if it's defined
    variables = get_evaluation_context().variables
    date_variables = variables.get('date', None)
    return val_to_date(date_variables['today']) if date_variables else timezone.now().date()


def f_weekday(date):
    """
    Returns the day of the week of a date (1 for Sunday to 7 for Saturday)
    """
    return ((val_to_date_or_datetime(date).weekday() + 1) % 7) + 1


def f_year(date):
    """
    Returns only the year of a date
    """
    return val_to_date_or_datetime(date).year


#################################### Math ####################################


def f_abs(number):
    """
    Returns the absolute value of a number
    """
    return val_to_decimal(abs(val_to_decimal(number)))


def f_max(*args):
    """
    Returns the maximum value of all arguments
    """
    result = val_to_decimal(args[0])
    for arg in args[1:]:
        arg = val_to_decimal(arg)
        if arg > result:
            result = arg
    return result


def f_min(*args):
    """
    Returns the minimum value of all arguments
    """
    result = val_to_decimal(args[0])
    for arg in args[1:]:
        arg = val_to_decimal(arg)
        if arg < result:
            result = arg
    return result


def f_power(number, power):
    """
    Returns the result of a number raised to a power
    """
    return val_to_decimal(math.pow(val_to_decimal(number), val_to_decimal(power)))


def f_sum(*args):
    """
    Returns the sum of all arguments
    """
    result = Decimal(0)
    for arg in args:
        result += val_to_decimal(arg)
    return result


#################################### Logical ####################################

def f_and(*args):
    """
    Returns TRUE if and only if all its arguments evaluate to TRUE
    """
    for arg in args:
        if not val_to_boolean(arg):
            return False
    return True


def f_false():
    """
    Returns the logical value FALSE
    """
    return False


def f_if(logical_test, value_if_true=0, value_if_false=False):
    """
    Returns one value if the condition evaluates to TRUE, and another value if it evaluates to FALSE
    """
    return value_if_true if val_to_boolean(logical_test) else value_if_false


def f_or(*args):
    """
    Returns TRUE if any argument is TRUE
    """
    for arg in args:
        if val_to_boolean(arg):
            return True
    return False


def f_true():
    """
    Returns the logical value TRUE
    """
    return True


#################################### Custom (non Excel) ####################################

def f_first_word(text):
    """
    Returns the first word in the given text string
    """
    # In Excel this would be IF(ISERR(FIND(" ",A2)),"",LEFT(A2,FIND(" ",A2)-1))
    return f_word(text, 1)


def f_percent(number):
    """
    Formats a number as a percentage
    """
    return '%d%%' % int(val_to_decimal(number) * 100)


def f_read_digits(text):
    """
    Formats digits in text for reading in TTS
    """

    def chunk(value, chunk_size):
        return [value[i: i + chunk_size] for i in range(0, len(value), chunk_size)]

    text = val_to_string(text).strip()
    if not text:
        return ''

    # trim off the plus for phone numbers
    if text[0] == '+':
        text = text[1:]

    length = len(text)

    # ssn
    if length == 9:
        result = ' '.join(text[:3])
        result += ' , ' + ' '.join(text[3:5])
        result += ' , ' + ' '.join(text[5:])
        return result

    # triplets, most international phone numbers
    if length % 3 == 0 and length > 3:
        chunks = chunk(text, 3)
        return ' '.join(','.join(chunks))

    # quads, credit cards
    if length % 4 == 0:
        chunks = chunk(text, 4)
        return ' '.join(','.join(chunks))

    # otherwise, just put a comma between each number
    return ','.join(text)


def f_remove_first_word(text):
    """
    Removes the first word from the given text string
    """
    text = val_to_string(text).lstrip()
    first_word = f_first_word(text)
    return text[len(first_word):].lstrip() if first_word else ''


def f_word(text, number, by_spaces=False):
    """
    Extracts the nth word from the given text string
    """
    return f_word_slice(text, number, val_to_integer(number) + 1, by_spaces)


def f_word_count(text, by_spaces=False):
    """
    Returns the number of words in the given text string
    """
    text = val_to_string(text)
    by_spaces = val_to_boolean(by_spaces)
    return len(get_words(text, by_spaces))


def f_word_slice(text, start, stop=0, by_spaces=False):
    """
    Extracts a substring spanning from start up to but not-including stop
    """
    text = val_to_string(text)
    start = val_to_integer(start)
    stop = val_to_integer(stop)
    by_spaces = val_to_boolean(by_spaces)

    if start == 0:
        raise ValueError("Start word cannot be zero")
    elif start > 0:
        start -= 1  # convert to a zero-based offset

    if stop == 0:  # zero is treated as no end
        stop = None
    elif stop > 0:
        stop -= 1  # convert to a zero-based offset

    words = get_words(text, by_spaces)

    selection = operator.getitem(words, slice(start, stop))

    # re-combine selected words with a single space
    return ' '.join(selection)

def f_field(text, index, delimiter=' '):
    """
    Reference a field in string separated by a delimiter
    :param text: the text to split
    :param index: which index in the result to return
    :param delimiter: the character to split by
    """
    splits = text.split(delimiter)

    # remove our delimiters and whitespace
    splits = [field for field in splits if field != delimiter and len(field.strip()) > 0]

    index = val_to_integer(index)
    if index < 1:
        raise ValueError('Field index cannot be less than 1')

    if index <= len(splits):
        return splits[index-1]

    return ''


#################################### Helper (not available in expressions) ####################################


def get_words(text, by_spaces):
    """
    Helper function which splits the given text string into words. If by_spaces is false, then text like
    '01-02-2014' will be split into 3 separate words. For backwards compatibility, this is the default for all
    expression functions.
    :param text: the text to split
    :param by_spaces: whether words should be split only by spaces or by punctuation like '-', '.' etc
    """
    rexp = r'\s+' if by_spaces else r'\W+'
    splits = regex.split(rexp, text, flags=regex.MULTILINE | regex.UNICODE | regex.V0)
    return [split for split in splits if split]   # return only non-empty
