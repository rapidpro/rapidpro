# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import requests

from django.conf import settings
from django.utils import timezone
from .serialize import (
    serialize_contact, serialize_label, serialize_field, serialize_channel, serialize_flow, serialize_group,
    serialize_location_hierarchy, serialize_environment, serialize_message
)


class RequestBuilder(object):
    def __init__(self, client, asset_timestamp):
        self.client = client
        self.asset_timestamp = asset_timestamp
        self.request = {'assets': [], 'events': []}

    def include_all(self, org):
        request = self
        for f in org.flows.filter(is_active=True, is_archived=False):
            request = request.include_flow(f)
        for channel in org.channels.filter(is_active=True):
            request = request.include_channel(channel)
        request = request.include_fields(org).include_groups(org).include_labels(org)
        if org.country_id:
            request = request.include_country(org)

        return request

    def include_channel(self, channel):
        self.request['assets'].append({
            'type': "channel",
            'url': get_assets_url(channel.org, self.asset_timestamp, 'channel', str(channel.uuid)),
            'content': serialize_channel(channel)
        })
        return self

    def include_fields(self, org):
        from temba.contacts.models import ContactField

        self.request['assets'].append({
            'type': "field",
            'url': get_assets_url(org, self.asset_timestamp, 'field'),
            'content': [serialize_field(f) for f in ContactField.objects.filter(org=org, is_active=True)],
            'is_set': True
        })
        return self

    def include_flow(self, flow):
        self.request['assets'].append({
            'type': "flow",
            'url': get_assets_url(flow.org, self.asset_timestamp, 'flow', str(flow.uuid)),
            'content': serialize_flow(flow)
        })
        return self

    def include_groups(self, org):
        from temba.contacts.models import ContactGroup

        self.request['assets'].append({
            'type': "group",
            'url': get_assets_url(org, self.asset_timestamp, 'group'),
            'content': [serialize_group(g) for g in ContactGroup.get_user_groups(org)],
            'is_set': True
        })
        return self

    def include_labels(self, org):
        from temba.msgs.models import Label

        self.request['assets'].append({
            'type': "label",
            'url': get_assets_url(org, self.asset_timestamp, 'label'),
            'content': [serialize_label(l) for l in Label.label_objects.filter(org=org, is_active=True)],
            'is_set': True
        })
        return self

    def include_country(self, org):
        self.request['assets'].append({
            'type': "location_hierarchy",
            'url': get_assets_url(org, self.asset_timestamp, 'location_hierarchy'),
            'content': serialize_location_hierarchy(org.country, org)
        })
        return self

    def add_environment_changed(self, org):
        """
        Notify the engine that the environment has changed
        """
        self.request['events'].append({
            'type': "environment_changed",
            'created_on': timezone.now().isoformat(),
            'environment': serialize_environment(org)
        })
        return self

    def add_contact_changed(self, contact):
        """
        Notify the engine that the contact has changed
        """
        self.request['events'].append({
            'type': "contact_changed",
            'created_on': timezone.now().isoformat(),
            'contact': serialize_contact(contact)
        })
        return self

    def add_msg_received(self, msg):
        """
        Notify the engine that an incoming message has been received from the session contact
        """
        self.request['events'].append({
            'type': "msg_received",
            'created_on': timezone.now().isoformat(),
            'msg': serialize_message(msg)
        })
        return self

    def add_run_expired(self, run):
        """
        Notify the engine that the active run in this session has expired
        """
        self.request['events'].append({
            'type': "run_expired",
            'created_on': run.exited_on.isoformat(),
            'run_uuid': str(run.uuid),
        })
        return self

    def asset_server(self, org):
        type_urls = {
            'channel': get_assets_url(org, self.asset_timestamp, 'channel'),
            'field': get_assets_url(org, self.asset_timestamp, 'field'),
            'flow': get_assets_url(org, self.asset_timestamp, 'flow'),
            'group': get_assets_url(org, self.asset_timestamp, 'group'),
            'label': get_assets_url(org, self.asset_timestamp, 'label'),
        }

        if org.country_id:
            type_urls['location_hierarchy'] = get_assets_url(org, self.asset_timestamp, 'location_hierarchy')

        self.request['asset_server'] = {'type_urls': type_urls}
        return self

    def start_manual(self, org, contact, flow, params=None):
        """
        User is manually starting this session
        """
        trigger = {
            'type': 'manual',
            'environment': serialize_environment(org),
            'contact': serialize_contact(contact),
            'flow': {'uuid': str(flow.uuid), 'name': flow.name},
            'params': params,
            'triggered_on': timezone.now().isoformat()
        }
        if params:
            trigger['params'] = params

        self.request['trigger'] = trigger

        return self.client.start(self.request)

    def start_by_flow_action(self, org, contact, flow, parent_run_summary):
        """
        New session was triggered by a flow action in a different run
        """
        self.request['trigger'] = {
            'type': 'flow_action',
            'environment': serialize_environment(org),
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
    class LogEntry(object):

        @classmethod
        def from_json(cls, entry_json):
            return cls(entry_json.get('step_uuid'), entry_json.get('action_uuid'), entry_json['event'])

        def __init__(self, step_uuid, action_uuid, event):
            self.step_uuid = step_uuid
            self.action_uuid = action_uuid
            self.event = event

        def as_json(self):
            return dict(step_uuid=self.step_uuid, action_uuid=self.action_uuid, event=self.event)

    @classmethod
    def from_json(cls, output_json):
        return cls(output_json['session'], [Output.LogEntry.from_json(e) for e in output_json.get('log', [])])

    def __init__(self, session, log):
        self.session = session
        self.log = log

    def as_json(self):
        return dict(session=self.session, log=[entry.as_json() for entry in self.log])


class FlowServerException(Exception):
    pass


class FlowServerClient:
    """
    Basic client for GoFlow's flow server
    """
    def __init__(self, base_url, debug=False):
        self.base_url = base_url
        self.debug = debug

    def request_builder(self, asset_timestamp):
        return RequestBuilder(self, asset_timestamp)

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

        response = requests.post("%s/flow/%s" % (self.base_url, endpoint), json=payload)
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


def get_assets_url(org, timestamp, asset_type=None, asset_uuid=None):
    if settings.TESTING:
        url = 'http://localhost:8000/flow/assets/%d/%d/' % (org.id, timestamp)
    else:  # pragma: no cover
        url = 'https://%s/flow/assets/%d/%d/' % (settings.HOSTNAME, org.id, timestamp)

    if asset_type:
        url = url + asset_type + '/'
    if asset_uuid:
        url = url + asset_uuid + '/'
    return url


def get_client():
    return FlowServerClient(settings.FLOW_SERVER_URL, settings.FLOW_SERVER_DEBUG)
