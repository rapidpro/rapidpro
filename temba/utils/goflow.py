from __future__ import unicode_literals, absolute_import, print_function

import json
import requests
import six

from django.conf import settings
from django.db.models import Prefetch
from django.utils.timezone import now
from mptt.utils import get_cached_trees
from temba.utils import datetime_to_str


def serialize_flow(flow, strip_ui=True):
    """
    Migrates the given flow, returning None if the flow or any of its dependencies can't be run in
    goflow because of unsupported features.
    """
    from temba.contacts.models import ContactField, ContactURN
    from temba.flows.models import get_flow_user

    flow_def = flow.as_json(expand_contacts=True)

    # contact fields need to have UUIDs in the new world
    for actionset in flow_def.get('action_sets', []):
        for action in actionset.get('actions', []):
            if action['type'] == 'save':
                field_key = action['field']
                if field_key in ('name', 'first_name', 'tel_e164') or field_key in ContactURN.CONTEXT_KEYS_TO_SCHEME.keys():
                    continue

                field = ContactField.get_or_create(flow.org, get_flow_user(flow.org), field_key)
                action['field_uuid'] = str(field.uuid)

    migrated_flow_def = get_client().migrate({'flows': [flow_def]})[0]

    if strip_ui:
        del migrated_flow_def['_ui']

    return migrated_flow_def


def serialize_channel(channel):
    return {
        'uuid': channel.uuid,
        'name': six.text_type(channel.get_name()),
        'type': channel.channel_type,
        'address': channel.address
    }


def serialize_group(group):
    return {'uuid': group.uuid, 'name': group.name, 'query': group.query}


def serialize_country(org):
    """
    Serializes country data for the given org, e.g.
    {
        "name": "Rwanda",
        "children": [
            {
                "name": "Kigali City",
                "children": [
                    {
                        "name": "Nyarugenge",
                        "children": []
                    }
                ]
            },
            ...
    """
    from temba.locations.models import BoundaryAlias

    if not org.country:
        return

    queryset = org.country.get_descendants(include_self=True)
    queryset = queryset.prefetch_related(
        Prefetch('aliases', queryset=BoundaryAlias.objects.filter(org=org)),
    )

    def _serialize_node(node):
        names = [node.name]
        for alias in node.aliases.all():
            names.append(alias.name)

        rendered = {'names': names}

        children = node.get_children()
        if children:
            rendered['children'] = []
            for child in node.get_children():
                rendered['children'].append(_serialize_node(child))
        return rendered

    return [_serialize_node(node) for node in get_cached_trees(queryset)][0]


class RequestBuilder(object):
    def __init__(self, client):
        self.client = client
        self.request = {'assets': [], 'events': []}

    def include_flow(self, flow):
        self.request['assets'].append({
            'type': "flow",
            'url': get_assets_url(flow.org, 'flow', str(flow.uuid)),
            'content': serialize_flow(flow)
        })
        return self

    def include_channel(self, channel):
        self.request['assets'].append({
            'type': "channel",
            'url': get_assets_url(channel.org, 'channel', str(channel.uuid)),
            'content': serialize_channel(channel)
        })
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

    def set_contact(self, contact):
        from temba.msgs.models import Msg
        from temba.values.models import Value

        org_fields = {f.id: f for f in contact.org.contactfields.filter(is_active=True)}
        values = Value.objects.filter(contact=contact, contact_field_id__in=org_fields.keys())
        field_values = {}
        for v in values:
            field = org_fields[v.contact_field_id]
            field_values[field.key] = {
                'field_uuid': str(field.uuid),
                'field_name': field.label,
                'value': v.string_value
            }

        _contact, contact_urn = Msg.resolve_recipient(contact.org, None, contact, None)

        event = {
            'type': "set_contact",
            'created_on': datetime_to_str(now()),
            'contact': {
                'uuid': contact.uuid,
                'name': contact.name,
                'urns': [urn.urn for urn in contact.urns.all()],
                'groups': [{"uuid": group.uuid, "name": group.name} for group in contact.user_groups.all()],
                'timezone': "UTC",
                'language': contact.language,
                'fields': field_values
            }
        }

        # only populate channel if this contact can actually be reached (ie, has a URN)
        if contact_urn:
            channel = contact.org.get_send_channel(contact_urn=contact_urn)
            if channel:
                event['contact']['channel_uuid'] = channel.uuid

        self.request['events'].append(event)
        return self

    def set_extra(self, extra):
        self.request['events'].append({
            'type': "set_extra",
            'created_on': datetime_to_str(now()),
            'extra': extra
        })
        return self

    def msg_received(self, msg):
        event = {
            'type': "msg_received",
            'created_on': datetime_to_str(msg.created_on),
            'text': msg.text,
            'contact_uuid': str(msg.contact.uuid),

        }
        if msg.contact_urn:
            event['urn'] = msg.contact_urn.urn
        if msg.channel:
            event['channel_uuid'] = str(msg.channel.uuid)
        if msg.attachments:
            event['attachments'] = msg.attachments

        self.request['events'].append(event)
        return self

    def run_expired(self, run):
        self.request['events'].append({
            'type': "run_expired",
            'created_on': datetime_to_str(run.exited_on),
            'run_uuid': str(run.uuid),
        })
        return self

    def asset_urls(self, org):
        self.request['asset_urls'] = {
            'channel': get_assets_url(org, 'channel'),
            'flow': get_assets_url(org, 'flow'),
            'group': get_assets_url(org, 'group'),
        }
        return self

    def start(self, flow):
        self.request['flow_uuid'] = str(flow.uuid)

        return self.client.start(self.request)

    def resume(self, session):
        self.request['session'] = session

        return self.client.resume(self.request)


class Output(object):
    class LogEntry(object):
        def __init__(self, step_uuid, action_uuid, event):
            self.step_uuid = step_uuid
            self.action_uuid = action_uuid
            self.event = event

        @classmethod
        def from_json(cls, entry_json):
            return cls(entry_json['step_uuid'], entry_json.get('action_uuid'), entry_json['event'])

    def __init__(self, session, log):
        self.session = session
        self.log = log

    @classmethod
    def from_json(cls, output_json):
        return cls(output_json['session'], [Output.LogEntry.from_json(e) for e in output_json.get('log', [])])


class FlowServerException(Exception):
    pass


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


def get_assets_url(org, asset_type=None, asset_uuid=None):
    if settings.TESTING:
        url = 'http://localhost:8000/flow_assets/%d' % org.id
    else:
        url = 'https://%s/flow_assets/%d' % (settings.HOSTNAME, org.id)

    if asset_type:
        url = url + '/' + asset_type
    if asset_uuid:
        url = url + '/' + asset_uuid
    return url


def get_client():
    return FlowServerClient(settings.FLOW_SERVER_URL, settings.FLOW_SERVER_DEBUG)
