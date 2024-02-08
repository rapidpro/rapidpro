import re

from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator


class TembaEmailValidator(EmailValidator):
    user_regex = re.compile(
        r"(^[-!#$%&'*+/=?^_`{}|~0-9A-Z]+(\.[-!#$%&'*+/=?^_`{}|~0-9A-Z]+)*\Z"  # dot-atom
        r'|^"([\001-\010\013\014\016-\[\]-\177]|\\[\001-\011\013\014\016-\177])*"\Z)',  # quoted-string
        re.IGNORECASE,
    )
    domain_regex = re.compile(
        # max length for domain name labels is 63 characters per RFC 1034
        r"(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,63}|[A-Z0-9-]{2,}(?<!-))\Z",
        re.IGNORECASE,
    )


temba_validate_email = TembaEmailValidator()


def is_valid_address(address):
    """
    Very loose email address check
    """
    if not address:
        return False

    try:
        temba_validate_email(address)
    except ValidationError:
        return False

    return True
