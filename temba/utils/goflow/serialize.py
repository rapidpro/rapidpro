# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import six

from django.db.models import Prefetch
from mptt.utils import get_cached_trees
from six.moves.urllib.parse import urlencode
from temba.values.models import Value

VALUE_TYPE_NAMES = {c[0]: c[2] for c in Value.TYPE_CONFIG}
VALUE_TYPE_NAMES['N'] = 'number'


def serialize_flow(flow, strip_ui=True):
    """
    Migrates the given flow, returning None if the flow or any of its dependencies can't be run in
    goflow because of unsupported features.
    """
    from .client import get_client

    flow.ensure_current_version()
    flow_def = flow.as_json(expand_contacts=True)

    migrated_flow_def = get_client().migrate({'flows': [flow_def]})[0]

    if strip_ui:
        del migrated_flow_def['_ui']

    return migrated_flow_def


def serialize_channel(channel):
    from temba.channels.models import Channel

    return {
        'uuid': str(channel.uuid),
        'name': six.text_type(channel.get_name()),
        'address': channel.address,
        'schemes': channel.schemes,
        'roles': [Channel.ROLE_CONFIG[r] for r in channel.role]
    }


def serialize_channel_ref(channel):
    return {'uuid': str(channel.uuid), 'name': channel.name}


def serialize_contact(contact):
    from temba.contacts.models import URN

    field_values = {}
    for field in contact.org.cached_contact_fields.values():
        field_values[field.key] = contact.get_field_json(field)

    # augment URN values with preferred channel UUID as a parameter
    urn_values = []
    for u in contact.urns.order_by('-priority', 'id'):
        # for each URN we resolve the preferred channel and include that as a query param
        channel = contact.org.get_send_channel(contact_urn=u)
        if channel:
            scheme, path, query, display = URN.to_parts(u.urn)
            urn_str = URN.from_parts(scheme, path, query=urlencode({'channel': str(channel.uuid)}), display=display)
        else:
            urn_str = u.urn

        urn_values.append(urn_str)

    return {
        'uuid': contact.uuid,
        'name': contact.name,
        'language': contact.language,
        'timezone': "UTC",
        'urns': urn_values,
        'groups': [serialize_group_ref(group) for group in contact.user_groups.filter(is_active=True)],
        'fields': field_values
    }


def serialize_environment(org):
    languages = [org.primary_language.iso_code] if org.primary_language else []

    return {
        'date_format': "DD-MM-YYYY" if org.date_format == 'D' else "MM-DD-YYYY",
        'time_format': "tt:mm",
        'timezone': six.text_type(org.timezone),
        'languages': languages
    }


def serialize_field(field):
    return {'key': field.key, 'name': field.label, 'value_type': VALUE_TYPE_NAMES[field.value_type]}


def serialize_group(group):
    return {'uuid': str(group.uuid), 'name': group.name, 'query': group.query}


def serialize_group_ref(group):
    return {'uuid': str(group.uuid), 'name': group.name}


def serialize_label(label):
    return {'uuid': str(label.uuid), 'name': label.name}


def serialize_location_hierarchy(country, aliases_from_org=None):
    """
    Serializes a country as a location hierarchy, e.g.
    {
        "name": "Rwanda",
        "children": [
            {
                "name": "Kigali City",
                "aliases": ["Kigali", "Kigari"],
                "children": [
                    ...
                ]
            }
        ]
    }
    """
    queryset = country.get_descendants(include_self=True)

    if aliases_from_org:
        from temba.locations.models import BoundaryAlias

        queryset = queryset.prefetch_related(
            Prefetch('aliases', queryset=BoundaryAlias.objects.filter(org=aliases_from_org)),
        )

    def _serialize_node(node):
        rendered = {'name': node.name}

        if aliases_from_org:
            rendered['aliases'] = [a.name for a in node.aliases.all()]

        children = node.get_children()
        if children:
            rendered['children'] = []
            for child in node.get_children():
                rendered['children'].append(_serialize_node(child))
        return rendered

    return [_serialize_node(node) for node in get_cached_trees(queryset)][0]


def serialize_message(msg):
    serialized = {
        'uuid': str(msg.uuid),
        'text': msg.text,
    }

    if msg.contact_urn_id:
        serialized['urn'] = msg.contact_urn.urn
    if msg.channel_id:
        serialized['channel'] = serialize_channel_ref(msg.channel)
    if msg.attachments:
        serialized['attachments'] = msg.attachments

    return serialized
