from __future__ import unicode_literals, absolute_import, print_function

import json
import requests
import six

from django.conf import settings
from django.db.models import Prefetch
from django.utils.timezone import now
from mptt.utils import get_cached_trees
from temba.utils import datetime_to_str


class FlowServerException(Exception):
    pass


def migrate_flow_with_dependencies(flow, extra_flows=(), strip_ui=True):
    """
    Migrates the given flow with its dependencies, returning [] if the flow or any of its dependencies can't be run in
    goflow because of unsupported features.
    """
    from temba.contacts.models import ContactField, ContactURN
    from temba.flows.models import Flow, get_flow_user

    legacy_flows = flow.org.resolve_dependencies([flow], [], False)

    for extra_flow in extra_flows:
        if extra_flow not in legacy_flows:
            legacy_flows.add(extra_flow)

    legacy_flow_defs = []
    for flow in legacy_flows:
        # if any flow can't be run in goflow, bail
        if not flow.is_runnable_in_goflow() or flow.flow_type not in (Flow.MESSAGE, Flow.FLOW):
            return []

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

        legacy_flow_defs.append(flow_def)

    migrated_flow_defs = get_client().migrate({'flows': legacy_flow_defs})

    if strip_ui:
        for migrated_flow_def in migrated_flow_defs:
            del migrated_flow_def['_ui']

    return migrated_flow_defs


class RequestBuilder(object):
    def __init__(self, client):
        self.client = client
        self.request = {'assets': [], 'events': []}

    def include_flows(self, flows):
        def as_asset(f):
            return {'type': "flow", 'content': f, 'url': "http://rpd.io/todo"}

        self.request['assets'].extend([as_asset(flow) for flow in flows])
        return self

    def include_channels(self, channels):
        def as_asset(c):
            return {
                'type': "channel",
                'content': {
                    'uuid': c.uuid,
                    'name': six.text_type(c.get_name()),
                    'type': c.channel_type,
                    'address': c.address
                },
                'url': "http://rpd.io/todo"
            }

        self.request['assets'].extend([as_asset(ch) for ch in channels])
        return self

    def include_locations(self, org):
        """
        Adds country data for the given org as a single location_set asset. e.g.
        {
            "type": "location_set",
            "content": {
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

        self.request['assets']['location_sets'] = [_serialize_node(node) for node in get_cached_trees(queryset)]
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
                'language': contact.language,
                'fields': field_values,
                'channel_uuid': channel_uuid
            }
        })
        return self

    def set_extra(self, extra):
        self.request['events'].append({
            'type': "set_extra",
            'created_on': datetime_to_str(now()),
            'extra': extra
        })
        return self

    def msg_received(self, msg):
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
            'attachments': msg.attachments or [],
            'contact_uuid': str(msg.contact.uuid),
            'channel_uuid': channel_uuid
        })
        return self

    def run_expired(self, run):
        self.request['events'].append({
            'type': "run_expired",
            'created_on': datetime_to_str(run.exited_on),
            'run_uuid': str(run.uuid),
        })
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
