# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import requests

from django.conf import settings
from django.utils import timezone
from enum import Enum
from .serialize import (
    serialize_contact, serialize_label, serialize_field, serialize_channel, serialize_flow, serialize_group,
    serialize_location_hierarchy, serialize_environment, serialize_message
)


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
    webhook_called = 23


class RequestBuilder(object):
    def __init__(self, client, org, base_assets_url):
        self.client = client
        self.org = org
        self.base_assets_url = base_assets_url
        self.request = {'assets': [], 'events': []}

    def include_all(self, simulator=False):
        request = self
        for f in self.org.flows.filter(is_active=True, is_archived=False):
            request = request.include_flow(f)

        request = (
            request.
            include_fields()
            .include_groups()
            .include_labels()
            .include_channels(simulator)
        )
        if self.org.country_id:
            request = request.include_country()

        return request

    def include_channels(self, simulator):
        from temba.channels.models import Channel

        channels = [serialize_channel(c) for c in self.org.channels.filter(is_active=True)]
        if simulator:
            channels.append(Channel.SIMULATOR_CHANNEL)

        self.request['assets'].append({
            'type': "channel_set",
            'url': '%s/channel/?simulator=%d' % (self.base_assets_url, 1 if simulator else 0),
            'content': channels
        })
        return self

    def include_fields(self):
        from temba.contacts.models import ContactField

        self.request['assets'].append({
            'type': "field_set",
            'url': '%s/field/' % self.base_assets_url,
            'content': [serialize_field(f) for f in ContactField.objects.filter(org=self.org, is_active=True)]
        })
        return self

    def include_flow(self, flow):
        self.request['assets'].append({
            'type': "flow",
            'url': '%s/flow/%s/' % (self.base_assets_url, str(flow.uuid)),
            'content': serialize_flow(flow)
        })
        return self

    def include_groups(self):
        from temba.contacts.models import ContactGroup

        self.request['assets'].append({
            'type': "group_set",
            'url': '%s/group/' % self.base_assets_url,
            'content': [serialize_group(g) for g in ContactGroup.get_user_groups(self.org)]
        })
        return self

    def include_labels(self):
        from temba.msgs.models import Label

        self.request['assets'].append({
            'type': "label_set",
            'url': '%s/label/' % self.base_assets_url,
            'content': [serialize_label(l) for l in Label.label_objects.filter(org=self.org, is_active=True)]
        })
        return self

    def include_country(self):
        self.request['assets'].append({
            'type': "location_hierarchy",
            'url': '%s/location_hierarchy/' % self.base_assets_url,
            'content': serialize_location_hierarchy(self.org.country, self.org)
        })
        return self

    def add_environment_changed(self):
        """
        Notify the engine that the environment has changed
        """
        self.request['events'].append({
            'type': Events.environment_changed.name,
            'created_on': timezone.now().isoformat(),
            'environment': serialize_environment(self.org)
        })
        return self

    def add_contact_changed(self, contact):
        """
        Notify the engine that the contact has changed
        """
        self.request['events'].append({
            'type': Events.contact_changed.name,
            'created_on': contact.modified_on.isoformat(),
            'contact': serialize_contact(contact)
        })
        return self

    def add_msg_received(self, msg):
        """
        Notify the engine that an incoming message has been received from the session contact
        """
        self.request['events'].append({
            'type': Events.msg_received.name,
            'created_on': msg.created_on.isoformat(),
            'msg': serialize_message(msg)
        })
        return self

    def add_run_expired(self, run):
        """
        Notify the engine that the active run in this session has expired
        """
        self.request['events'].append({
            'type': Events.run_expired.name,
            'created_on': run.exited_on.isoformat(),
            'run_uuid': str(run.uuid),
        })
        return self

    def asset_server(self, simulator=False):
        type_urls = {
            'flow': '%s/flow/{uuid}/' % self.base_assets_url,
            'channel_set': '%s/channel/?simulator=%d' % (self.base_assets_url, 1 if simulator else 0),
            'field_set': '%s/field/' % self.base_assets_url,
            'group_set': '%s/group/' % self.base_assets_url,
            'label_set': '%s/label/' % self.base_assets_url
        }

        if self.org.country_id:
            type_urls['location_hierarchy'] = '%s/location_hierarchy/' % self.base_assets_url

        self.request['asset_server'] = {'type_urls': type_urls}
        return self

    def start_manual(self, contact, flow, params=None):
        """
        User is manually starting this session
        """
        trigger = {
            'type': 'manual',
            'environment': serialize_environment(self.org),
            'contact': serialize_contact(contact),
            'flow': {'uuid': str(flow.uuid), 'name': flow.name},
            'params': params,
            'triggered_on': timezone.now().isoformat()
        }
        if params:
            trigger['params'] = params

        self.request['trigger'] = trigger

        return self.client.start(self.request)

    def start_by_flow_action(self, contact, flow, parent_run_summary):
        """
        New session was triggered by a flow action in a different run
        """
        self.request['trigger'] = {
            'type': 'flow_action',
            'environment': serialize_environment(self.org),
            'contact': serialize_contact(contact),
            'flow': {'uuid': str(flow.uuid), 'name': flow.name},
            'triggered_on': timezone.now().isoformat(),
            'run': parent_run_summary
        }

        return self.client.start(self.request)

    def resume(self, session):
        """
        Resume the given existing session
        """
        self.request['session'] = session

        return self.client.resume(self.request)


class Output(object):
    @classmethod
    def from_json(cls, output_json):
        return cls(output_json['session'], output_json.get('events', []))

    def __init__(self, session, events):
        self.session = session
        self.events = events

    def as_json(self):
        return dict(session=self.session, events=self.events)


class FlowServerException(Exception):
    pass


class FlowServerClient(object):
    """
    Basic client for GoFlow's flow server
    """
    headers = {'User-Agent': 'Temba'}

    def __init__(self, base_url, debug=False):
        self.base_url = base_url
        self.debug = debug

    def request_builder(self, org, asset_timestamp):
        assets_host = 'http://localhost:8000' if settings.TESTING else ('https://%s' % settings.HOSTNAME)
        base_assets_url = '%s/flow/assets/%d/%d' % (assets_host, org.id, asset_timestamp)

        return RequestBuilder(self, org, base_assets_url)

    def start(self, flow_start):
        return Output.from_json(self._request('start', flow_start))

    def resume(self, flow_resume):
        return Output.from_json(self._request('resume', flow_resume))

    def migrate(self, flow_migrate):
        return self._request('migrate', flow_migrate)

    def _request(self, endpoint, payload):
        if self.debug:
            print('[GOFLOW]=============== %s request ===============' % endpoint)
            print(json.dumps(payload, indent=2))
            print('[GOFLOW]=============== /%s request ===============' % endpoint)

        response = requests.post("%s/flow/%s" % (self.base_url, endpoint), json=payload, headers=self.headers)
        resp_json = response.json()

        if self.debug:
            print('[GOFLOW]=============== %s response ===============' % endpoint)
            print(json.dumps(resp_json, indent=2))
            print('[GOFLOW]=============== /%s response ===============' % endpoint)

        if 400 <= response.status_code < 500:
            errors = "\n".join(resp_json['errors'])
            raise FlowServerException("Invalid request: " + errors)

        response.raise_for_status()

        return resp_json


def get_client():
    return FlowServerClient(settings.FLOW_SERVER_URL, settings.FLOW_SERVER_DEBUG)
