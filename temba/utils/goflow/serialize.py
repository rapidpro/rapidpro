# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import six

from django.db.models import Prefetch
from mptt.utils import get_cached_trees
from six.moves.urllib.parse import urlencode
from temba.values.models import Value

VALUE_TYPE_NAMES = {c[0]: c[2] for c in Value.TYPE_CONFIG}


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

    serialized = {
        'uuid': str(channel.uuid),
        'name': six.text_type(channel.get_name()),
        'address': channel.address,
        'schemes': channel.schemes,
        'roles': [Channel.ROLE_CONFIG[r] for r in channel.role]
    }

    if channel.parent_id:
        serialized['parent'] = serialize_channel_ref(channel.parent)

    return serialized


def serialize_channel_ref(channel):
    return {'uuid': str(channel.uuid), 'name': channel.name}


def serialize_contact(contact):
    from temba.contacts.models import Contact, URN
    from temba.values.models import Value

    org_fields = {f.id: f for f in contact.org.contactfields.filter(is_active=True)}
    values = Value.objects.filter(contact=contact, contact_field_id__in=org_fields.keys())
    field_values = {}
    for v in values:
        field = org_fields[v.contact_field_id]
        field_values[field.key] = {
            'value': Contact.serialize_field_value(field, v),
            'created_on': v.created_on.isoformat()
        }

    # augment URN values with preferred channel UUID as a parameter
    urn_values = []
    for u in contact.urns.order_by('-priority', 'id'):
        scheme, path, query, display = URN.to_parts(u.urn)
        query = urlencode({'channel': str(u.channel.uuid)}) if u.channel_id else None
        urn_values.append(URN.from_parts(scheme, path, query=query, display=display))

    return {
        'uuid': contact.uuid,
        'name': contact.name,
        'language': contact.language,
        'timezone': "UTC",
        'urns': urn_values,
        'groups': [serialize_group_ref(group) for group in contact.user_groups.all()],
        'fields': field_values
    }


def serialize_environment(org):
    languages = [org.primary_language.iso_code] if org.primary_language else []

    return {
        'date_format': "dd-MM-yyyy" if org.date_format == 'D' else "MM-dd-yyyy",
        'time_format': "hh:mm",
        'timezone': six.text_type(org.timezone),
        'languages': languages
    }


def serialize_field(field):
    return {'key': field.key, 'label': field.label, 'value_type': VALUE_TYPE_NAMES[field.value_type]}


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
        'created_on': msg.created_on.isoformat(),
        'text': msg.text,
    }

    if msg.contact_urn:
        serialized['urn'] = msg.contact_urn.urn
    if msg.channel:
        serialized['channel'] = serialize_channel_ref(msg.channel)
    if msg.attachments:
        serialized['attachments'] = msg.attachments

    return serialized
