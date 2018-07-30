
import json
from enum import Enum

import requests

from django.conf import settings
from django.utils import timezone

from .assets import get_asset_urls
from .serialize import serialize_contact, serialize_environment, serialize_location_hierarchy, serialize_message


class Events(Enum):
    broadcast_created = 1
    contact_changed = 2
    contact_channel_changed = 3
    contact_field_changed = 4
    contact_groups_added = 5
    contact_groups_removed = 6
    contact_language_changed = 7
    contact_name_changed = 8
    contact_timezone_changed = 9
    contact_urn_added = 10
    email_created = 11
    environment_changed = 12
    error = 13
    flow_triggered = 14
    input_labels_added = 15
    msg_created = 16
    msg_received = 17
    msg_wait = 18
    nothing_wait = 19
    run_expired = 20
    run_result_changed = 21
    session_triggered = 22
    wait_timed_out = 23
    webhook_called = 24


class RequestBuilder:
    def __init__(self, client, org, base_assets_url):
        self.client = client
        self.org = org
        self.base_assets_url = base_assets_url
        self.request = {"assets": [], "events": [], "config": {}}

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
        from temba.channels.models import Channel
        from .assets import ChannelSetType

        serialized = ChannelSetType().serialize_active(self.org, simulator)

        if simulator:
            serialized["content"].append(Channel.SIMULATOR_CHANNEL)

        self.request["assets"].append(serialized)
        return self

    def include_fields(self):
        from .assets import FieldSetType

        self.request["assets"].append(FieldSetType().serialize_active(self.org))
        return self

    def include_flow(self, flow):
        from .assets import FlowType

        self.request["assets"].append(FlowType().serialize_item(flow.org, str(flow.uuid)))
        return self

    def include_groups(self):
        from .assets import GroupSetType

        self.request["assets"].append(GroupSetType().serialize_active(self.org))
        return self

    def include_labels(self):
        from .assets import LabelSetType

        self.request["assets"].append(LabelSetType().serialize_active(self.org))
        return self

    def include_country(self):
        from .assets import LocationHierarchyType

        self.request["assets"].append(
            {
                "type": "location_hierarchy",
                "url": LocationHierarchyType().get_url(self.org, simulator=False),
                "content": serialize_location_hierarchy(self.org.country, self.org),
            }
        )
        return self

    def include_resthooks(self):
        from .assets import ResthookSetType

        self.request["assets"].append(ResthookSetType().serialize_active(self.org))
        return self

    def add_environment_changed(self):
        """
        Notify the engine that the environment has changed
        """
        self.request["events"].append(
            {
                "type": Events.environment_changed.name,
                "created_on": timezone.now().isoformat(),
                "environment": serialize_environment(self.org),
            }
        )
        return self

    def add_contact_changed(self, contact):
        """
        Notify the engine that the contact has changed
        """
        self.request["events"].append(
            {
                "type": Events.contact_changed.name,
                "created_on": contact.modified_on.isoformat(),
                "contact": serialize_contact(contact),
            }
        )
        return self

    def add_msg_received(self, msg):
        """
        Notify the engine that an incoming message has been received from the session contact
        """
        self.request["events"].append(
            {"type": Events.msg_received.name, "created_on": msg.created_on.isoformat(), "msg": serialize_message(msg)}
        )
        return self

    def add_run_expired(self, run):
        """
        Notify the engine that the active run in this session has expired
        """
        self.request["events"].append(
            {"type": Events.run_expired.name, "created_on": run.exited_on.isoformat(), "run_uuid": str(run.uuid)}
        )
        return self

    def add_wait_timed_out(self):
        """
        Notify the engine that the session wait timed out
        """
        self.request["events"].append({"type": Events.wait_timed_out.name, "created_on": timezone.now().isoformat()})
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
        trigger = {
            "type": "manual",
            "environment": serialize_environment(self.org),
            "contact": serialize_contact(contact),
            "flow": {"uuid": str(flow.uuid), "name": flow.name},
            "params": params,
            "triggered_on": timezone.now().isoformat(),
        }
        if params:
            trigger["params"] = params

        self.request["trigger"] = trigger

        return self.client.start(self.request)

    def start_by_flow_action(self, contact, flow, parent_run_summary):
        """
        New session was triggered by a flow action in a different run
        """
        self.request["trigger"] = {
            "type": "flow_action",
            "environment": serialize_environment(self.org),
            "contact": serialize_contact(contact),
            "flow": {"uuid": str(flow.uuid), "name": flow.name},
            "triggered_on": timezone.now().isoformat(),
            "run": parent_run_summary,
        }

        return self.client.start(self.request)

    def resume(self, session):
        """
        Resume the given existing session
        """
        self.request["session"] = session

        return self.client.resume(self.request)


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
