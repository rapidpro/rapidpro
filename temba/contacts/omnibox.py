# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import operator

from django.db.models import Q
from django.db.models.functions import Upper
from temba.contacts.models import Contact, ContactGroup, ContactGroupCount, ContactURN
from temba.msgs.models import Label
from six.moves import reduce

SEARCH_ALL_GROUPS = 'g'
SEARCH_STATIC_GROUPS = 's'
SEARCH_CONTACTS = 'c'
SEARCH_URNS = 'u'


def omnibox_query(org, **kwargs):
    """
    Performs a omnibox query based on the given arguments
    """
    # determine what type of group/contact/URN lookup is being requested
    contact_uuids = kwargs.get('c', None)  # contacts with ids
    message_ids = kwargs.get('m', None)  # contacts with message ids
    label_id = kwargs.get('l', None)     # contacts in flow step with UUID
    group_uuids = kwargs.get('g', None)    # groups with ids
    urn_ids = kwargs.get('u', None)      # URNs with ids
    search = kwargs.get('search', None)  # search of groups, contacts and URNs
    types = list(kwargs.get('types', ''))    # limit search to types (g | s | c | u)

    # these lookups return a Contact queryset
    if contact_uuids or message_ids or label_id:
        qs = Contact.objects.filter(org=org, is_blocked=False, is_stopped=False, is_active=True, is_test=False)

        if contact_uuids:
            qs = qs.filter(uuid__in=contact_uuids.split(","))

        elif message_ids:
            qs = qs.filter(msgs__in=message_ids.split(","))

        elif label_id:
            label = Label.label_objects.get(pk=label_id)
            qs = qs.filter(msgs__in=label.get_messages())

        return qs.distinct().order_by('name')

    # this lookup returns a ContactGroup queryset
    elif group_uuids:
        return ContactGroup.user_groups.filter(org=org, uuid__in=group_uuids.split(",")).order_by('name')

    # this lookup returns a ContactURN queryset
    elif urn_ids:
        qs = ContactURN.objects.filter(org=org, id__in=urn_ids.split(",")).select_related('contact')
        return qs.order_by('path')

    # searching returns something which acts enough like a queryset to be paged
    return omnibox_mixed_search(org, search, types)


def term_search(queryset, fields, terms):
    term_queries = []
    for term in terms:
        field_queries = []
        for field in fields:
            field_queries.append(Q(**{field: term}))
        term_queries.append(reduce(operator.or_, field_queries))

    return queryset.filter(reduce(operator.and_, term_queries))


def omnibox_mixed_search(org, search, types):
    """
    Performs a mixed group, contact and URN search, returning the first N matches of each type.
    """
    search_terms = search.split(' ') if search else None
    search_types = types or (SEARCH_ALL_GROUPS, SEARCH_CONTACTS, SEARCH_URNS)
    per_type_limit = 25
    results = []

    if SEARCH_ALL_GROUPS in search_types or SEARCH_STATIC_GROUPS in search_types:
        groups = ContactGroup.get_user_groups(org)

        # exclude dynamic groups if not searching all groups
        if SEARCH_ALL_GROUPS not in search_types:
            groups = groups.filter(query=None)

        if search:
            groups = term_search(groups, ('name__icontains',), search_terms)

        results += list(groups.order_by(Upper('name'))[:per_type_limit])

    if SEARCH_CONTACTS in search_types:
        contacts = Contact.objects.filter(org=org, is_active=True, is_blocked=False, is_stopped=False, is_test=False)

        try:
            search_id = int(search)
        except (ValueError, TypeError):
            search_id = None

        if org.is_anon and search_id is not None:
            contacts = contacts.filter(id=search_id)
        elif search:
            contacts = term_search(contacts, ('name__icontains',), search_terms)

        results += list(contacts.order_by(Upper('name'))[:per_type_limit])

    if SEARCH_URNS in search_types:
        # only include URNs that are send-able
        from temba.channels.models import Channel
        allowed_schemes = org.get_schemes(Channel.ROLE_SEND) if not org.is_anon else []

        urns = ContactURN.objects.filter(org=org, scheme__in=allowed_schemes).exclude(contact=None)

        if search:
            urns = term_search(urns, ('path__icontains',), search_terms)

        results += list(urns.prefetch_related('contact').order_by(Upper('path'))[:per_type_limit])

    return results  # sorted(results, key=lambda o: o.name if hasattr(o, 'name') else o.path)


def omnibox_results_to_dict(org, results):
    """
    Converts the result of a omnibox query (queryset of contacts, groups or URNs, or a list) into a dict {id, text}
    """
    formatted = []

    groups = [r for r in results if isinstance(r, ContactGroup)]
    group_counts = ContactGroupCount.get_totals(groups) if groups else {}

    for obj in results:
        if isinstance(obj, ContactGroup):
            result = {
                'id': 'g-%s' % obj.uuid,
                'text': obj.name,
                'extra': group_counts[obj]
            }
        elif isinstance(obj, Contact):
            result = {
                'id': 'c-%s' % obj.uuid,
                'text': obj.get_display(org)
            }
        elif isinstance(obj, ContactURN):
            result = {
                'id': 'u-%d' % obj.id,
                'text': obj.get_display(org),
                'extra': obj.contact.name or None,
                'scheme': obj.scheme
            }

        formatted.append(result)

    return formatted
