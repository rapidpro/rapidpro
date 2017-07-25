from __future__ import unicode_literals, absolute_import, print_function

import json
import requests
import six

from django.conf import settings
from django.utils.timezone import now
from temba.utils import datetime_to_str


class FlowServerException(Exception):
    pass


class RequestBuilder(object):
    def __init__(self, client):
        self.client = client
        self.request = {'assets': {'flows': [], 'channels': []}, 'events': []}

    def include_flows(self, flows):
        self.request['assets']['flows'].extend(flows)
        return self

    def include_channels(self, channels):
        def as_asset(c):
            return {'uuid': c.uuid, 'name': six.text_type(c.get_name()), 'type': c.channel_type, 'address': c.address}

        self.request['assets']['channels'].extend([as_asset(ch) for ch in channels])
        return self

    def set_environment(self, org):
        languages = [org.primary_language.iso_code] if org.primary_language else []

        self.request['events'].append({
            'type': "set_environment",
            'created_on': datetime_to_str(now()),
            'date_format': "dd-MM-yyyy" if org.date_format == 'D' else "MM-dd-yyyy",
            'time_format': "hh:mm",
            'timezone': six.text_type(org.timezone),
            'languages': languages
        })
        return self

    def set_input(self, msg):
        urn = None
        if msg.contact_urn:
            urn = msg.contact_urn.urn

        # simulation doesn't have a channel
        channel_uuid = None
        if msg.channel:
            channel_uuid = str(msg.channel.uuid)

        self.request['events'].append({
            'type': "msg_received",
            'created_on': datetime_to_str(msg.created_on),
            'urn': urn,
            'text': msg.text,
            'attachments': [],
            'contact_uuid': str(msg.contact.uuid),
            'channel_uuid': channel_uuid
        })
        return self

    def set_contact(self, contact):
        # TODO include fields
        #   "fields": {
        #     "gender": {
        #       "field_uuid": "501d8dc4-3fb7-45c1-acb8-626f87535a99",
        #       "field_name": "Gender",
        #       "value": "Male"
        #     }

        # only include language if it's valid org language
        if contact.language and contact.language in contact.org.get_language_codes():
            language = contact.language
        else:
            language = None

        from temba.msgs.models import Msg
        _contact, contact_urn = Msg.resolve_recipient(contact.org, None, contact, None)

        # only populate channel if this contact can actually be reached (ie, has a URN)
        channel_uuid = None
        if contact_urn:
            channel = contact.org.get_send_channel(contact_urn=contact_urn)
            if channel:
                channel_uuid = channel.uuid

        self.request['events'].append({
            'type': "set_contact",
            'created_on': datetime_to_str(now()),
            'contact': {
                'uuid': contact.uuid,
                'name': contact.name,
                'urns': [urn.urn for urn in contact.urns.all()],
                'groups': [{"uuid": group.uuid, "name": group.name} for group in contact.user_groups.all()],
                'timezone': "UTC",
                'language': language,
                'fields': {},
                'channel_uuid': channel_uuid
            }
        })
        return self

    def start(self, flow):
        self.request['flow_uuid'] = str(flow.uuid)

        return self.client.start(self.request)

    def resume(self, session):
        self.request['session'] = session

        return self.client.resume(self.request)


class Output(object):
    def __init__(self, session, log):
        self.session = session
        self.log = log

    @classmethod
    def from_json(cls, output_json):
        return cls(output_json['session'], output_json.get('log', []))


class FlowServerClient:
    """
    Basic client for GoFlow's flow server
    """
    def __init__(self, base_url, debug=False):
        self.base_url = base_url
        self.debug = debug

    def request_builder(self):
        return RequestBuilder(self)

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


def get_client():
    return FlowServerClient(settings.FLOW_SERVER_URL, settings.FLOW_SERVER_DEBUG)
