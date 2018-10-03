
from enum import Enum

import requests

from django.conf import settings
from django.utils import timezone

from temba.utils import json

from .assets import (
    ChannelType,
    FieldType,
    FlowType,
    GroupType,
    LabelType,
    LocationHierarchyType,
    ResthookType,
    get_asset_type,
    get_asset_urls,
)
from .serialize import serialize_contact, serialize_environment, serialize_message, serialize_ref


class Events(Enum):
    broadcast_created = 1
    contact_changed = 2
    contact_channel_changed = 3
    contact_field_changed = 4
    contact_groups_changed = 5
    contact_language_changed = 6
    contact_name_changed = 7
    contact_timezone_changed = 8
    contact_urn_added = 9
    email_created = 10
    environment_changed = 11
    error = 12
    flow_triggered = 13
    input_labels_added = 14
    msg_created = 15
    msg_received = 16
    msg_wait = 17
    nothing_wait = 18
    run_expired = 19
    run_result_changed = 20
    session_triggered = 21
    wait_timed_out = 22
    webhook_called = 23


class RequestBuilder:
    def __init__(self, client, org, base_assets_url):
        self.client = client
        self.org = org
        self.base_assets_url = base_assets_url
        self.request = {"assets": [], "config": {}}

    def include_all(self, simulator=False):
        request = self
        for f in self.org.flows.filter(is_active=True, is_archived=False):
            request = request.include_flow(f)

        request = (
            request.include_fields().include_groups().include_labels().include_channels(simulator).include_resthooks()
        )
        if self.org.country_id:
            request = request.include_country()

        return request

    def include_channels(self, simulator):
        self.request["assets"].append(get_asset_type(ChannelType).bundle_set(self.org, simulator))
        return self

    def include_fields(self):
        self.request["assets"].append(get_asset_type(FieldType).bundle_set(self.org))
        return self

    def include_flow(self, flow):
        self.request["assets"].append(get_asset_type(FlowType).bundle_item(flow.org, str(flow.uuid)))
        return self

    def include_groups(self):
        self.request["assets"].append(get_asset_type(GroupType).bundle_set(self.org))
        return self

    def include_labels(self):
        self.request["assets"].append(get_asset_type(LabelType).bundle_set(self.org))
        return self

    def include_country(self):
        self.request["assets"].append(get_asset_type(LocationHierarchyType).bundle_set(self.org))
        return self

    def include_resthooks(self):
        self.request["assets"].append(get_asset_type(ResthookType).bundle_set(self.org))
        return self

    def add_msg_received(self, msg):
        """
        Notify the engine that an incoming message has been received from the session contact
        """
        self.request["events"].append(
            {"type": Events.msg_received.name, "created_on": msg.created_on.isoformat(), "msg": serialize_message(msg)}
        )
        return self

    def asset_server(self, simulator=False):
        self.request["asset_server"] = {"type_urls": get_asset_urls(self.org, simulator)}
        return self

    def set_config(self, name, value):
        self.request["config"][name] = value
        return self

    def start_manual(self, contact, flow, params=None):
        """
        User is manually starting this session
        """
        self.request["trigger"] = self._base_trigger("manual", timezone.now().isoformat(), contact, flow)
        self.request["trigger"]["params"] = params

        return self.client.start(self.request)

    def start_by_msg(self, contact, flow, msg):
        """
        New session was triggered by a new message from the contact
        """
        self.request["trigger"] = self._base_trigger("msg", timezone.now().isoformat(), contact, flow)
        self.request["trigger"]["msg"] = serialize_message(msg)

        return self.client.start(self.request)

    def start_by_flow_action(self, contact, flow, parent_run_summary):
        """
        New session was triggered by a flow action in a different run
        """
        self.request["trigger"] = self._base_trigger("flow_action", timezone.now().isoformat(), contact, flow)
        self.request["trigger"]["run"] = parent_run_summary

        return self.client.start(self.request)

    def start_by_campaign(self, contact, flow, event):
        """
        New session was triggered by a campaign event
        """
        self.request["trigger"] = self._base_trigger("campaign", timezone.now().isoformat(), contact, flow)
        self.request["trigger"]["event"] = {"uuid": str(event.uuid), "campaign": serialize_ref(event.campaign)}

        return self.client.start(self.request)

    def resume_by_msg(self, session, msg, contact=None):
        """
        Resume an existing session because of a new message
        """
        from temba.msgs.models import Msg

        if isinstance(msg, Msg):
            resumed_on = msg.created_on.isoformat()
            msg = serialize_message(msg)
        else:
            resumed_on = timezone.now().isoformat()

        self.request["resume"] = self._base_resume("msg", resumed_on, contact)
        self.request["resume"]["msg"] = msg
        self.request["session"] = session

        return self.client.resume(self.request)

    def resume_by_run_expiration(self, session, run, contact=None):
        """
        Resume an existing session because of a run expiration
        """
        self.request["resume"] = self._base_resume("run_expiration", run.exited_on.isoformat(), contact)
        self.request["session"] = session

        return self.client.resume(self.request)

    def resume_by_wait_timeout(self, session, contact=None):
        """
        Resume an existing session because of a wait timeout
        """
        self.request["resume"] = self._base_resume("wait_timeout", timezone.now().isoformat(), contact)
        self.request["session"] = session

        return self.client.resume(self.request)

    def _base_trigger(self, type_name, triggered_on, contact, flow):
        return {
            "type": type_name,
            "triggered_on": triggered_on,
            "contact": serialize_contact(contact),
            "environment": serialize_environment(self.org),
            "flow": {"uuid": str(flow.uuid), "name": flow.name},
        }

    @staticmethod
    def _base_resume(type_name, resumed_on, contact=None):
        resume = {"type": type_name, "resumed_on": resumed_on}
        if contact:
            resume["contact"] = serialize_contact(contact)
        return resume


class Output:
    @classmethod
    def from_json(cls, output_json):
        return cls(output_json["session"], output_json.get("events", []))

    def __init__(self, session, events):
        self.session = session
        self.events = events

    def as_json(self):
        return dict(session=self.session, events=self.events)


class FlowServerException(Exception):
    def __init__(self, endpoint, request, response):
        self.endpoint = endpoint
        self.request = request
        self.response = response

    def as_json(self):
        return {"endpoint": self.endpoint, "request": self.request, "response": self.response}


class FlowServerClient:
    """
    Basic client for GoFlow's flow server
    """

    headers = {"User-Agent": "Temba"}

    def __init__(self, base_url, debug=False):
        self.base_url = base_url
        self.debug = debug

    def request_builder(self, org):
        assets_host = "http://localhost:8000" if settings.TESTING else ("https://%s" % settings.HOSTNAME)
        base_assets_url = "%s/flow/assets/%d" % (assets_host, org.id)

        return RequestBuilder(self, org, base_assets_url)

    def start(self, flow_start):
        return Output.from_json(self._request("start", flow_start))

    def resume(self, flow_resume):
        return Output.from_json(self._request("resume", flow_resume))

    def migrate(self, flow_migrate):
        return self._request("migrate", flow_migrate)

    def _request(self, endpoint, payload):
        if self.debug:
            print("[GOFLOW]=============== %s request ===============" % endpoint)
            print(json.dumps(payload, indent=2))
            print("[GOFLOW]=============== /%s request ===============" % endpoint)

        response = requests.post("%s/flow/%s" % (self.base_url, endpoint), json=payload, headers=self.headers)
        resp_json = response.json()

        if self.debug:
            print("[GOFLOW]=============== %s response ===============" % endpoint)
            print(json.dumps(resp_json, indent=2))
            print("[GOFLOW]=============== /%s response ===============" % endpoint)

        if 400 <= response.status_code < 500:
            raise FlowServerException(endpoint, payload, resp_json)

        response.raise_for_status()

        return resp_json


def get_client():
    return FlowServerClient(settings.FLOW_SERVER_URL, settings.FLOW_SERVER_DEBUG)
