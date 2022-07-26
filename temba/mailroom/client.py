import logging
from dataclasses import asdict, dataclass, field

import requests

from django.conf import settings

from temba.utils import json

from .modifiers import Modifier

logger = logging.getLogger(__name__)


class MailroomException(Exception):
    """
    Exception for failed requests to mailroom
    """

    def __init__(self, endpoint, request, response):
        self.endpoint = endpoint
        self.request = request
        self.response = response

    def as_json(self):
        return {"endpoint": self.endpoint, "request": self.request, "response": self.response}


class FlowValidationException(MailroomException):
    """
    Exception for a flow validation request that fails validation
    """

    def __init__(self, endpoint, request, response):
        super().__init__(endpoint, request, response)

        self.message = response["error"]

    def __str__(self):
        return self.message


@dataclass
class ContactSpec:
    """
    Describes a contact to be created
    """

    name: str
    language: str
    urns: list[str]
    fields: dict[str, str]
    groups: list[str]


@dataclass
class QueryInclusions:
    group_uuids: list = field(default_factory=list)
    contact_uuids: list = field(default_factory=list)
    urns: list = field(default_factory=list)
    query: str = ""


@dataclass
class QueryExclusions:
    non_active: bool = False  # contacts who are blocked, stopped or archived
    in_a_flow: bool = False  # contacts who are currently in a flow (including this one)
    started_previously: bool = False  # contacts who have been in this flow in the last 90 days
    not_seen_since_days: int = 0  # contacts who have not been seen for more than this number of days


@dataclass(frozen=True)
class QueryMetadata:
    """
    Contact query metadata
    """

    attributes: list = field(default_factory=list)
    schemes: list = field(default_factory=list)
    fields: list = field(default_factory=list)
    groups: list = field(default_factory=list)
    allow_as_group: bool = False


@dataclass(frozen=True)
class ParsedQuery:
    query: str
    elastic_query: dict
    metadata: QueryMetadata


@dataclass(frozen=True)
class SearchResults:
    query: str
    total: int
    contact_ids: list
    metadata: QueryMetadata


@dataclass(frozen=True)
class StartPreview:
    query: str
    total: int
    sample_ids: list
    metadata: QueryMetadata


class MailroomClient:
    """
    Basic web client for mailroom
    """

    default_headers = {"User-Agent": "Temba"}

    def __init__(self, base_url, auth_token):
        self.base_url = base_url
        self.headers = self.default_headers.copy()
        if auth_token:
            self.headers["Authorization"] = "Token " + auth_token

    def version(self):
        return self._request("", post=False).get("version")

    def expression_migrate(self, expression):
        """
        Migrates a legacy expression to latest engine version
        """
        if not expression:
            return ""

        try:
            resp = self._request("expression/migrate", {"expression": expression})
            return resp["migrated"]
        except FlowValidationException:
            # if the expression is invalid.. just return original
            return expression

    def flow_migrate(self, definition, to_version=None):
        """
        Migrates a flow definition to the specified spec version
        """
        from temba.flows.models import Flow

        if not to_version:
            to_version = Flow.CURRENT_SPEC_VERSION

        return self._request("flow/migrate", {"flow": definition, "to_version": to_version}, encode_json=True)

    def flow_inspect(self, org_id, flow):
        payload = {"flow": flow}

        # can't do dependency checking during tests because mailroom can't see unit test data created in a transaction
        if not settings.TESTING:
            payload["org_id"] = org_id

        return self._request("flow/inspect", payload, encode_json=True)

    def flow_change_language(self, flow, language):
        payload = {"flow": flow, "language": language}

        return self._request("flow/change_language", payload, encode_json=True)

    def flow_clone(self, flow, dependency_mapping):
        payload = {"flow": flow, "dependency_mapping": dependency_mapping}

        return self._request("flow/clone", payload)

    def flow_preview_start(
        self,
        org_id: int,
        flow_id: int,
        include: QueryInclusions,
        exclude: QueryExclusions,
        sample_size: int,
    ) -> StartPreview:
        payload = {
            "org_id": org_id,
            "flow_id": flow_id,
            "include": asdict(include),
            "exclude": asdict(exclude),
            "sample_size": sample_size,
        }

        response = self._request("flow/preview_start", payload, encode_json=True)
        return StartPreview(
            query=response["query"],
            total=response["total"],
            sample_ids=response["sample_ids"],
            metadata=QueryMetadata(**response.get("metadata", {})),
        )

    def msg_resend(self, org_id, msg_ids):
        payload = {"org_id": org_id, "msg_ids": msg_ids}

        return self._request("msg/resend", payload)

    def po_export(self, org_id: int, flow_ids: list, language: str):
        payload = {"org_id": org_id, "flow_ids": flow_ids, "language": language}

        return self._request("po/export", payload, returns_json=False)

    def po_import(self, org_id, flow_ids, language, po_data):
        payload = {"org_id": org_id, "flow_ids": flow_ids, "language": language}

        return self._request("po/import", payload, files={"po": po_data})

    def sim_start(self, payload):
        return self._request("sim/start", payload, encode_json=True)

    def sim_resume(self, payload):
        return self._request("sim/resume", payload, encode_json=True)

    def contact_create(self, org_id: int, user_id: int, contact: ContactSpec):
        payload = {
            "org_id": org_id,
            "user_id": user_id,
            "contact": asdict(contact),
        }

        return self._request("contact/create", payload)

    def contact_modify(self, org_id, user_id, contact_ids, modifiers: list[Modifier]):
        payload = {
            "org_id": org_id,
            "user_id": user_id,
            "contact_ids": contact_ids,
            "modifiers": [asdict(m) for m in modifiers],
        }

        return self._request("contact/modify", payload)

    def contact_resolve(self, org_id: int, channel_id: int, urn: str):
        payload = {"org_id": org_id, "channel_id": channel_id, "urn": urn}

        return self._request("contact/resolve", payload)

    def contact_interrupt(self, org_id: int, user_id: int, contact_id: int):
        payload = {"org_id": org_id, "user_id": user_id, "contact_id": contact_id}

        return self._request("contact/interrupt", payload)

    def contact_search(self, org_id, group_uuid, query, sort, offset=0, exclude_ids=()) -> SearchResults:
        payload = {
            "org_id": org_id,
            "group_uuid": group_uuid,
            "exclude_ids": exclude_ids,
            "query": query,
            "sort": sort,
            "offset": offset,
        }
        response = self._request("contact/search", payload)
        return SearchResults(
            query=response["query"],
            total=response["total"],
            contact_ids=response["contact_ids"],
            metadata=QueryMetadata(**response.get("metadata", {})),
        )

    def parse_query(self, org_id: int, query: str, parse_only: bool = False, group_uuid: str = "") -> ParsedQuery:
        payload = {"org_id": org_id, "query": query, "parse_only": parse_only, "group_uuid": group_uuid}

        response = self._request("contact/parse_query", payload)
        return ParsedQuery(
            query=response["query"],
            elastic_query=response["elastic_query"],
            metadata=QueryMetadata(**response.get("metadata", {})),
        )

    def ticket_assign(self, org_id: int, user_id: int, ticket_ids: list, assignee_id: int, note: str):
        payload = {
            "org_id": org_id,
            "user_id": user_id,
            "ticket_ids": ticket_ids,
            "assignee_id": assignee_id,
            "note": note,
        }

        return self._request("ticket/assign", payload)

    def ticket_add_note(self, org_id: int, user_id: int, ticket_ids: list, note: str):
        payload = {"org_id": org_id, "user_id": user_id, "ticket_ids": ticket_ids, "note": note}

        return self._request("ticket/add_note", payload)

    def ticket_change_topic(self, org_id: int, user_id: int, ticket_ids: list, topic_id: int):
        payload = {"org_id": org_id, "user_id": user_id, "ticket_ids": ticket_ids, "topic_id": topic_id}

        return self._request("ticket/change_topic", payload)

    def ticket_close(self, org_id: int, user_id: int, ticket_ids: list, force: bool):
        payload = {"org_id": org_id, "user_id": user_id, "ticket_ids": ticket_ids, "force": force}

        return self._request("ticket/close", payload)

    def ticket_reopen(self, org_id, user_id, ticket_ids):
        payload = {"org_id": org_id, "user_id": user_id, "ticket_ids": ticket_ids}

        return self._request("ticket/reopen", payload)

    def _request(self, endpoint, payload=None, files=None, post=True, encode_json=False, returns_json=True):
        if logger.isEnabledFor(logging.DEBUG):  # pragma: no cover
            logger.debug("=============== %s request ===============" % endpoint)
            logger.debug(json.dumps(payload, indent=2))
            logger.debug("=============== /%s request ===============" % endpoint)

        headers = self.headers.copy()
        if files:
            kwargs = dict(data=payload, files=files)
        elif encode_json:
            # do the JSON encoding ourselves - required when the json is something we've loaded with our decoder
            # which could contain non-standard types
            headers["Content-Type"] = "application/json"
            kwargs = dict(data=json.dumps(payload))
        else:
            kwargs = dict(json=payload)

        req_fn = requests.post if post else requests.get
        response = req_fn("%s/mr/%s" % (self.base_url, endpoint), headers=headers, **kwargs)

        return_val = response.json() if returns_json else response.content

        if logger.isEnabledFor(logging.DEBUG):  # pragma: no cover
            logger.debug("=============== %s response ===============" % endpoint)
            logger.debug(return_val)
            logger.debug("=============== /%s response ===============" % endpoint)

        if response.status_code == 422:
            raise FlowValidationException(endpoint, payload, return_val)
        if 400 <= response.status_code < 500:
            raise MailroomException(endpoint, payload, return_val)

        response.raise_for_status()

        return return_val


def get_client() -> MailroomClient:
    return MailroomClient(settings.MAILROOM_URL, settings.MAILROOM_AUTH_TOKEN)
