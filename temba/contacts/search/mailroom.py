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


def parse_query(org, query: str, *, parse_only: bool = False, group=None) -> mailroom.ParsedQuery:
    """
    Parses the passed in query in the context of the org
    """
    try:
        group_uuid = group.uuid if group else None

        return mailroom.get_client().parse_query(org.id, query, parse_only=parse_only, group_uuid=str(group_uuid))
    except mailroom.MailroomException as e:
        raise SearchException.from_mailroom_exception(e)


def search_contacts(
    org, query: str, *, group=None, sort: str = None, offset: int = None, exclude_ids=()
) -> mailroom.SearchResults:
    try:
        group_id = group.id if group else None

        return mailroom.get_client().contact_search(
            org.id, group_id=group_id, query=query, sort=sort, offset=offset, exclude_ids=exclude_ids
        )
    except mailroom.MailroomException as e:
        raise SearchException.from_mailroom_exception(e)


def preview_broadcast(org, include: mailroom.Inclusions, exclude: mailroom.Exclusions) -> mailroom.BroadcastPreview:
    try:
        return mailroom.get_client().msg_preview_broadcast(org.id, include=include, exclude=exclude)
    except mailroom.MailroomException as e:
        raise SearchException.from_mailroom_exception(e)


def preview_start(org, flow, include: mailroom.Inclusions, exclude: mailroom.Exclusions) -> mailroom.StartPreview:
    try:
        return mailroom.get_client().flow_preview_start(org.id, flow.id, include=include, exclude=exclude)
    except mailroom.MailroomException as e:
        raise SearchException.from_mailroom_exception(e)
