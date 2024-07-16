from django.utils.translation import gettext_lazy as _


class RequestException(Exception):
    """
    Exception for requests to mailroom that return a non-422 error status.
    """

    def __init__(self, endpoint, request, response):
        self.endpoint = endpoint
        self.request = request
        self.response = response

        try:
            self.error = response.json().get("error")
        except Exception:
            self.error = response.content.decode("utf-8")

    def __str__(self):
        return self.error


class FlowValidationException(Exception):
    """
    Request that fails because the provided flow definition is invalid.
    """

    def __init__(self, error: str):
        self.error = error

    def __str__(self):
        return self.error


class QueryValidationException(Exception):
    """
    Request that fails because the provided contact query is invalid.
    """

    messages = {
        "syntax": _("Invalid query syntax."),
        "invalid_number": _("Unable to convert '%(value)s' to a number."),
        "invalid_date": _("Unable to convert '%(value)s' to a date."),
        "invalid_language": _("'%(value)s' is not a valid language code."),
        "invalid_flow": _("'%(value)s' is not a valid flow name."),
        "invalid_group": _("'%(value)s' is not a valid group name."),
        "invalid_partial_name": _("Using ~ with name requires token of at least %(min_token_length)s characters."),
        "invalid_partial_urn": _("Using ~ with URN requires value of at least %(min_value_length)s characters."),
        "unsupported_contains": _("Can only use ~ with name or URN values."),
        "unsupported_comparison": _("Can only use %(operator)s with number or date values."),
        "unsupported_setcheck": _("Can't check whether '%(property)s' is set or not set."),
        "unknown_property": _("Can't resolve '%(property)s' to a field or URN scheme."),
        "unknown_property_type": _("Prefixes must be 'fields' or 'urns'."),
        "redacted_urns": _("Can't query on URNs in an anonymous workspace."),
    }

    def __init__(self, error: str, code: str, extra: dict = None):
        self.error = error
        self.code = code
        self.extra = extra or {}

    def __str__(self):
        if self.code and self.code in self.messages:
            return self.messages[self.code] % self.extra

        return self.error


class URNValidationException(Exception):
    """
    Request that fails because the provided contact URN is invalid or taken.
    """

    def __init__(self, error: str, code: str, index: int):
        self.error = error
        self.code = code
        self.index = index

    def __str__(self):
        return self.error
