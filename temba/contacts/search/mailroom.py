from dataclasses import asdict, dataclass, field

from django.utils.encoding import force_str
from django.utils.translation import gettext_lazy as _

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
        "invalid_flow": _("'%(value)s' is not a valid flow name"),
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

        return force_str(self.message)


@dataclass(frozen=True)
class Metadata:
    attributes: list = field(default_factory=list)
    schemes: list = field(default_factory=list)
    fields: list = field(default_factory=list)
    groups: list = field(default_factory=list)
    allow_as_group: bool = False


@dataclass(frozen=True)
class ParsedQuery:
    query: str
    elastic_query: dict
    metadata: Metadata


def parse_query(org, query: str, *, parse_only: bool = False, group=None) -> ParsedQuery:
    """
    Parses the passed in query in the context of the org
    """
    try:
        group_uuid = group.uuid if group else None

        response = mailroom.get_client().parse_query(org.id, query, parse_only=parse_only, group_uuid=str(group_uuid))
        return ParsedQuery(response["query"], response["elastic_query"], Metadata(**response.get("metadata", {})))

    except mailroom.MailroomException as e:
        raise SearchException.from_mailroom_exception(e)


@dataclass(frozen=True)
class SearchResults:
    total: int
    query: str
    contact_ids: list
    metadata: Metadata


def search_contacts(
    org, query: str, *, group=None, sort: str = None, offset: int = None, exclude_ids=()
) -> SearchResults:
    try:
        group_uuid = group.uuid if group else None

        response = mailroom.get_client().contact_search(
            org.id, group_uuid=str(group_uuid), query=query, sort=sort, offset=offset, exclude_ids=exclude_ids
        )
        return SearchResults(
            response["total"], response["query"], response["contact_ids"], Metadata(**response.get("metadata", {}))
        )

    except mailroom.MailroomException as e:
        raise SearchException.from_mailroom_exception(e)


@dataclass
class Exclusions:
    non_active: bool = False  # contacts who are blocked, stopped or archived
    in_a_flow: bool = False  # contacts who are currently in a flow (including this one)
    started_previously: bool = False  # contacts who have been in this flow in the last 90 days
    not_seen_recently: bool = False  # contacts who have not been seen for more than 90 days


@dataclass(frozen=True)
class StartPreview:
    query: str
    total: int
    sample_ids: list
    metadata: Metadata


def preview_start(
    org, flow, group_uuids: list, contact_uuids: list, urns: list, query: str, exclusions: Exclusions, sample_size: int
) -> StartPreview:
    try:
        response = mailroom.get_client().flow_preview_start(
            org.id,
            flow.id,
            group_uuids=group_uuids,
            contact_uuids=contact_uuids,
            urns=urns,
            query=query,
            exclusions=asdict(exclusions),
            sample_size=sample_size,
        )
        return StartPreview(
            response["query"], response["total"], response["sample_ids"], Metadata(**response.get("metadata", {}))
        )

    except mailroom.MailroomException as e:
        raise SearchException.from_mailroom_exception(e)
