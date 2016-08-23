from __future__ import unicode_literals

from collections import namedtuple
from django.db.models import Count
from temba.contacts.models import Contact, ContactGroup, ContactURN
from temba.msgs.models import Label
from temba.utils import PageableQuery

RESULT_TYPE_GROUP = 1
RESULT_TYPE_CONTACT = 2
RESULT_TYPE_URN = 3


def omnibox_query(org, **kwargs):
    """
    Performs a omnibox query based on the given arguments
    """
    # determine what type of group/contact/URN lookup is being requested
    contact_uuids = kwargs.get('c', None)  # contacts with ids
    step_uuid = kwargs.get('s', None)    # contacts in flow step with UUID
    message_ids = kwargs.get('m', None)  # contacts with message ids
    label_id = kwargs.get('l', None)     # contacts in flow step with UUID
    group_uuids = kwargs.get('g', None)    # groups with ids
    urn_ids = kwargs.get('u', None)      # URNs with ids
    search = kwargs.get('search', None)  # search of groups, contacts and URNs
    types = kwargs.get('types', None)    # limit search to types (g | s | c | u)
    simulation = kwargs.get('simulation', 'false') == 'true'

    # these lookups return a Contact queryset
    if contact_uuids or step_uuid or message_ids or label_id:
        qs = Contact.objects.filter(org=org, is_blocked=False, is_active=True, is_test=simulation)

        if contact_uuids:
            qs = qs.filter(uuid__in=contact_uuids.split(","))

        elif step_uuid:
            from temba.flows.models import FlowStep
            steps = FlowStep.objects.filter(run__is_active=True, step_uuid=step_uuid,
                                            left_on=None, run__flow__org=org).distinct('contact').select_related('contact')
            contact_uuids = [f.contact.uuid for f in steps]
            qs = qs.filter(uuid__in=contact_uuids)

        elif message_ids:
            qs = qs.filter(msgs__in=message_ids.split(","))

        elif label_id:
            label = Label.label_objects.get(pk=label_id)
            qs = qs.filter(msgs__in=label.get_messages())

        return qs.distinct().order_by('name')

    # this lookup returns a ContactGroup queryset
    elif group_uuids:
        qs = ContactGroup.user_groups.filter(org=org, uuid__in=group_uuids.split(","))
        return qs.annotate(members=Count('contacts')).order_by('name')

    # this lookup returns a ContactURN queryset
    elif urn_ids:
        qs = ContactURN.objects.filter(org=org, id__in=urn_ids.split(",")).select_related('contact')
        return qs.order_by('path')

    # searching returns something which acts enough like a queryset to be paged
    return omnibox_mixed_search(org, search, types)


def omnibox_mixed_search(org, search, types):
    """
    Performs a mixed group, contact and URN search, returning a page-able query
    """
    from temba.channels.models import Channel

    search_terms = search.split(" ") if search else None

    if not types:
        types = 'gcu'

    # only include URNs that are send-able
    allowed_schemes = list(org.get_schemes(Channel.ROLE_SEND)) if not org.is_anon else None

    def add_search(col, _clauses, _params, by_id=False):
        """
        Adds a text search clause to the current query
        """
        term_clauses = []

        join_op = ' AND '
        for term in search_terms:
            term_clauses.append(col + " ILIKE %s")
            _params.append(r'%' + term + r'%')

            # if this is an anonymous org, maybe they are querying by id
            if by_id:
                try:
                    join_op = ' OR '
                    term_as_int = int(term)
                    term_clauses.append("id = %s")
                    _params.append(term_as_int)
                except ValueError:
                    pass

        _clauses.append('AND (' + join_op.join(term_clauses) + ')')

    # each unionised select returns 4 columns:
    # 1. id (prefixed with the type letter)
    # 2. text
    # 3. owner (contact name for URNs)
    # 4. scheme (only used for URNs)

    union_queries = []
    query_component = namedtuple('query_component', 'clauses params')

    if 'g' in types or 's' in types:
        group_query = """SELECT 1 AS type, g.uuid AS id, g.name AS text, NULL AS owner, NULL AS scheme
                         FROM contacts_contactgroup g
                         WHERE g.is_active = TRUE AND g.group_type = 'U' AND g.org_id = %s"""

        # do we include non-static groups?
        if 'g' not in types:
            group_query += " AND g.query IS NULL"

        clauses = [group_query]
        params = [org.pk]
        if search_terms:
            add_search('name', clauses, params)
        union_queries.append(query_component(clauses, params))

    if 'c' in types:
        clauses = ["""SELECT 2 AS type, c.uuid AS id, c.name AS text, NULL AS owner, NULL AS scheme
                      FROM contacts_contact c
                      WHERE c.is_active = TRUE AND c.is_blocked = FALSE AND c.is_test = FALSE AND c.org_id = %s"""]
        params = [org.pk]
        if search_terms:
            add_search('name', clauses, params, org.is_anon)
        union_queries.append(query_component(clauses, params))

    if 'u' in types and not org.is_anon and allowed_schemes:
        clauses = ["""SELECT 3 AS type, cast(cu.id as text) AS id, cu.path AS text, c.name AS owner, cu.scheme AS scheme
                      FROM contacts_contacturn cu
                      INNER JOIN contacts_contact c ON c.id = cu.contact_id
                      WHERE cu.org_id = %s AND cu.scheme = ANY(%s)"""]
        params = [org.pk, allowed_schemes]
        if search_terms:
            add_search('path', clauses, params)
        union_queries.append(query_component(clauses, params))

    # join all clauses and gather master list of parameters
    sql = ' UNION ALL '.join([' '.join(s.clauses) for s in union_queries])
    params = [p for s in union_queries for p in s.params]

    return PageableQuery(sql, ('type', 'text'), params)


def omnibox_results_to_dict(org, results):
    """
    Converts the result of a omnibox query (queryset of contacts, groups or URNs, or a list) into a dict {id, text}
    """
    formatted = []

    for obj in results:
        result = {}

        if isinstance(obj, dict):
            _id, text = obj['id'], obj['text']

            if obj['type'] == RESULT_TYPE_GROUP:
                result['id'] = 'g-%s' % _id
                result['text'] = text
                result['extra'] = ContactGroup.user_groups.get(uuid=_id).get_member_count()
            elif obj['type'] == RESULT_TYPE_CONTACT:
                result['id'] = 'c-%s' % _id
                if not text:
                    result['text'] = Contact.objects.get(uuid=_id).get_display(org)
                else:
                    result['text'] = text
            elif obj['type'] == RESULT_TYPE_URN:
                result['id'] = 'u-%s' % _id
                result['text'] = text
                result['extra'] = obj['owner']
                result['scheme'] = obj['scheme']
        elif isinstance(obj, ContactGroup):
            result['id'] = 'g-%s' % obj.uuid
            result['text'] = obj.name
            result['extra'] = obj.members
        elif isinstance(obj, Contact):
            result['id'] = 'c-%s' % obj.uuid
            result['text'] = obj.get_display(org)
        elif isinstance(obj, ContactURN):
            result['id'] = 'u-%d' % obj.pk
            result['text'] = obj.get_display(org)
            result['extra'] = obj.contact.get_display(org)
            result['scheme'] = obj.scheme

        formatted.append(result)

    return formatted
