import logging
from dataclasses import asdict

import requests

from django.conf import settings

from temba.contacts.models import Contact
from temba.msgs.models import Broadcast
from temba.utils import json

from ..modifiers import Modifier
from .exceptions import FlowValidationException, QueryValidationException, RequestException, URNValidationException
from .types import (
    ContactSpec,
    Exclusions,
    Inclusions,
    ParsedQuery,
    QueryMetadata,
    RecipientsPreview,
    ScheduleSpec,
    SearchResults,
    URNResult,
)

logger = logging.getLogger(__name__)


class MailroomClient:
    """
    Client for mailroom HTTP endpoints
    """

    default_headers = {"User-Agent": "Temba"}

    def __init__(self, base_url, auth_token):
        self.base_url = base_url
        self.headers = self.default_headers.copy()
        if auth_token:
            self.headers["Authorization"] = "Token " + auth_token

    def version(self):
        return self._request("", post=False).get("version")

    def android_event(self, org, channel, phone: str, event_type: str, extra: dict, occurred_on):
        return self._request(
            "android/event",
            {
                "org_id": org.id,
                "channel_id": channel.id,
                "phone": phone,
                "event_type": event_type,
                "extra": extra,
                "occurred_on": occurred_on.isoformat(),
            },
        )

    def android_message(self, org, channel, phone: str, text: str, received_on):
        return self._request(
            "android/message",
            {
                "org_id": org.id,
                "channel_id": channel.id,
                "phone": phone,
                "text": text,
                "received_on": received_on.isoformat(),
            },
        )

    def contact_create(self, org, user, contact: ContactSpec) -> Contact:
        resp = self._request("contact/create", {"org_id": org.id, "user_id": user.id, "contact": asdict(contact)})

        return Contact.objects.get(id=resp["contact"]["id"])

    def contact_export(self, org, group, query: str) -> list[int]:
        resp = self._request("contact/export", {"org_id": org.id, "group_id": group.id, "query": query})

        return resp["contact_ids"]

    def contact_export_preview(self, org, group, query: str) -> int:
        resp = self._request("contact/export_preview", {"org_id": org.id, "group_id": group.id, "query": query})

        return resp["total"]

    def contact_inspect(self, org, contacts) -> dict:
        resp = self._request("contact/inspect", {"org_id": org.id, "contact_ids": [c.id for c in contacts]})

        return {c: resp[str(c.id)] for c in contacts}

    def contact_interrupt(self, org, user, contact) -> int:
        resp = self._request("contact/interrupt", {"org_id": org.id, "user_id": user.id, "contact_id": contact.id})

        return resp["sessions"]

    def contact_modify(self, org, user, contacts, modifiers: list[Modifier]):
        return self._request(
            "contact/modify",
            {
                "org_id": org.id,
                "user_id": user.id,
                "contact_ids": [c.id for c in contacts],
                "modifiers": [asdict(m) for m in modifiers],
            },
        )

    def contact_parse_query(self, org, query: str, parse_only: bool = False) -> ParsedQuery:
        resp = self._request("contact/parse_query", {"org_id": org.id, "query": query, "parse_only": parse_only})

        return ParsedQuery(query=resp["query"], metadata=QueryMetadata(**resp.get("metadata", {})))

    def contact_search(self, org, group, query: str, sort: str, offset=0, limit=50, exclude_ids=()) -> SearchResults:
        resp = self._request(
            "contact/search",
            {
                "org_id": org.id,
                "group_id": group.id,
                "exclude_ids": exclude_ids,
                "query": query,
                "sort": sort,
                "offset": offset,
                "limit": limit,
            },
        )

        return SearchResults(
            query=resp["query"],
            total=resp["total"],
            contact_ids=resp["contact_ids"],
            metadata=QueryMetadata(**resp.get("metadata", {})),
        )

    def contact_urns(self, org, urns: list[str]):
        resp = self._request("contact/urns", {"org_id": org.id, "urns": urns})

        return [URNResult(**ur) for ur in resp["urns"]]

    def flow_change_language(self, definition: dict, language):
        return self._request("flow/change_language", {"flow": definition, "language": language}, encode_json=True)

    def flow_clone(self, definition: dict, dependency_mapping):
        return self._request("flow/clone", {"flow": definition, "dependency_mapping": dependency_mapping})

    def flow_inspect(self, org, definition: dict):
        payload = {"flow": definition}

        # can't do dependency checking during tests because mailroom can't see unit test data created in a transaction
        if not settings.TESTING:
            payload["org_id"] = org.id

        return self._request("flow/inspect", payload, encode_json=True)

    def flow_migrate(self, definition: dict, to_version=None):
        """
        Migrates a flow definition to the specified spec version
        """
        from temba.flows.models import Flow

        if not to_version:  # pragma: no cover
            to_version = Flow.CURRENT_SPEC_VERSION

        return self._request("flow/migrate", {"flow": definition, "to_version": to_version}, encode_json=True)

    def flow_start_preview(self, org, flow, include: Inclusions, exclude: Exclusions) -> RecipientsPreview:
        resp = self._request(
            "flow/start_preview",
            {
                "org_id": org.id,
                "flow_id": flow.id,
                "include": asdict(include),
                "exclude": asdict(exclude),
            },
        )

        return RecipientsPreview(query=resp["query"], total=resp["total"])

    def msg_broadcast(
        self,
        org,
        user,
        translations: dict,
        base_language: str,
        groups,
        contacts,
        urns: list,
        query: str,
        node_uuid: str,
        exclude: Exclusions,
        optin,
        template,
        template_variables: list,
        schedule: ScheduleSpec,
    ):
        resp = self._request(
            "msg/broadcast",
            {
                "org_id": org.id,
                "user_id": user.id,
                "translations": translations,
                "base_language": base_language,
                "group_ids": [g.id for g in groups],
                "contact_ids": [c.id for c in contacts],
                "urns": urns,
                "query": query,
                "node_uuid": node_uuid,
                "exclude": asdict(exclude) if exclude else None,
                "optin_id": optin.id if optin else None,
                "template_id": template.id if template else None,
                "template_variables": template_variables,
                "schedule": asdict(schedule) if schedule else None,
            },
        )

        return Broadcast.objects.get(id=resp["id"])

    def msg_broadcast_preview(self, org, include: Inclusions, exclude: Exclusions) -> RecipientsPreview:
        resp = self._request(
            "msg/broadcast_preview",
            {
                "org_id": org.id,
                "include": asdict(include),
                "exclude": asdict(exclude),
            },
        )

        return RecipientsPreview(query=resp["query"], total=resp["total"])

    def msg_handle(self, org, msgs):
        return self._request("msg/handle", {"org_id": org.id, "msg_ids": [m.id for m in msgs]})

    def msg_resend(self, org, msgs):
        return self._request("msg/resend", {"org_id": org.id, "msg_ids": [m.id for m in msgs]})

    def msg_send(self, org, user, contact, text: str, attachments: list[str], ticket):
        return self._request(
            "msg/send",
            {
                "org_id": org.id,
                "user_id": user.id,
                "contact_id": contact.id,
                "text": text,
                "attachments": attachments,
                "ticket_id": ticket.id if ticket else None,
            },
        )

    def po_export(self, org, flows, language: str):
        return self._request(
            "po/export",
            {
                "org_id": org.id,
                "flow_ids": [f.id for f in flows],
                "language": language,
            },
        )

    def po_import(self, org, flows, language: str, po_data):
        return self._request(
            "po/import",
            {
                "org_id": org.id,
                "flow_ids": [f.id for f in flows],
                "language": language,
            },
            files={"po": po_data},
        )

    def sim_start(self, payload: dict):
        return self._request("sim/start", payload, encode_json=True)

    def sim_resume(self, payload: dict):
        return self._request("sim/resume", payload, encode_json=True)

    def ticket_assign(self, org, user, tickets, assignee):
        return self._request(
            "ticket/assign",
            {
                "org_id": org.id,
                "user_id": user.id,
                "ticket_ids": [t.id for t in tickets],
                "assignee_id": assignee.id if assignee else None,
            },
        )

    def ticket_add_note(self, org, user, tickets, note: str):
        return self._request(
            "ticket/add_note",
            {
                "org_id": org.id,
                "user_id": user.id,
                "ticket_ids": [t.id for t in tickets],
                "note": note,
            },
        )

    def ticket_change_topic(self, org, user, tickets, topic):
        return self._request(
            "ticket/change_topic",
            {
                "org_id": org.id,
                "user_id": user.id,
                "ticket_ids": [t.id for t in tickets],
                "topic_id": topic.id,
            },
        )

    def ticket_close(self, org, user, tickets, force: bool):
        return self._request(
            "ticket/close",
            {
                "org_id": org.id,
                "user_id": user.id,
                "ticket_ids": [t.id for t in tickets],
                "force": force,
            },
        )

    def ticket_reopen(self, org, user, tickets):
        return self._request(
            "ticket/reopen",
            {
                "org_id": org.id,
                "user_id": user.id,
                "ticket_ids": [t.id for t in tickets],
            },
        )

    def _request(self, endpoint, payload=None, files=None, post=True, encode_json=False):
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

        if response.headers.get("Content-Type") == "application/json":
            resp_body = response.json()
        else:
            # not all endpoints return JSON, e.g. po file export
            resp_body = response.content

        if response.status_code == 422:
            error = resp_body["error"]
            domain, code = resp_body["code"].split(":")
            extra = resp_body.get("extra", {})

            if domain == "flow":
                raise FlowValidationException(error)
            elif domain == "query":
                raise QueryValidationException(error, code, extra)
            elif domain == "urn":
                raise URNValidationException(error, code, extra["index"])

        elif 400 <= response.status_code < 600:
            raise RequestException(endpoint, payload, response)

        return resp_body
