from typing import NamedTuple

from django.utils.encoding import force_text
from django.utils.translation import ugettext_lazy as _

from temba import mailroom


class SearchException(Exception):
    """
    Exception class for unparseable search queries
    """

    messages = {
        "unexpected_token": _("Invalid query syntax at '%(token)s'"),
        "invalid_number": _("Unable to convert '%(value)s' to a number"),
        "invalid_date": _("Unable to convert '%(value)s' to a date"),
        "invalid_language": _("'%(value)s' is not a valid language code"),
        "invalid_group": _("'%(value)s' is not a valid group name"),
        "invalid_partial_name": _("Using ~ with name requires token of at least %(min_token_length)s characters"),
        "invalid_partial_urn": _("Using ~ with URN requires value of at least %(min_value_length)s characters"),
        "unsupported_contains": _("Can only use ~ with name or URN values"),
        "unsupported_comparison": _("Can only use %(operator)s with number or date values"),
        "unsupported_setcheck": _("Can't check whether '%(property)s' is set or not set"),
        "unknown_property": _("Can't resolve '%(property)s' to a field or URN scheme"),
        "redacted_urns": _("Can't query on URNs in an anonymous workspace"),
    }

    def __init__(self, message, code=None, extra=None):
        self.message = message
        self.code = code
        self.extra = extra

    @classmethod
    def from_mailroom_exception(cls, e):
        return cls(e.response["error"], e.response.get("code"), e.response.get("extra", {}))

    def __str__(self):
        if self.code and self.code in self.messages:
            return self.messages[self.code] % self.extra

        return force_text(self.message)


class Metadata(NamedTuple):
    attributes: list = []
    schemes: list = []
    fields: list = []
    groups: list = []
    allow_as_group: bool = False


class ParsedQuery(NamedTuple):
    query: str
    elastic_query: dict
    metadata: Metadata = Metadata()


def parse_query(org, query: str, *, group=None) -> ParsedQuery:
    """
    Parses the passed in query in the context of the org
    """
    try:
        group_uuid = group.uuid if group else None

        response = mailroom.get_client().parse_query(org.id, query, group_uuid=str(group_uuid))
        return ParsedQuery(response["query"], response["elastic_query"], Metadata(**response.get("metadata", {})),)

    except mailroom.MailroomException as e:
        raise SearchException.from_mailroom_exception(e)


class SearchResults(NamedTuple):
    total: int
    query: str
    contact_ids: list
    metadata: Metadata = Metadata()


def search_contacts(
    org, query: str, *, group=None, sort: str = None, offset: int = None, exclude_ids=()
) -> SearchResults:
    try:
        group_uuid = group.uuid if group else None

        response = mailroom.get_client().contact_search(
            org.id, group_uuid=str(group_uuid), query=query, sort=sort, offset=offset, exclude_ids=exclude_ids,
        )
        return SearchResults(
            response["total"], response["query"], response["contact_ids"], Metadata(**response.get("metadata", {})),
        )

    except mailroom.MailroomException as e:
        raise SearchException.from_mailroom_exception(e)
