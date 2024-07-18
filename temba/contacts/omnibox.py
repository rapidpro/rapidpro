import json

from django.db.models.functions import Lower

from temba import mailroom
from temba.utils.models.es import IDSliceQuerySet

from .models import Contact, ContactGroup, ContactGroupCount

SEARCH_ALL_GROUPS = "g"
SEARCH_STATIC_GROUPS = "s"
SEARCH_CONTACTS = "c"


def omnibox_query(org, **kwargs):
    """
    Performs a omnibox query based on the given arguments
    """

    # determine what type of group/contact/URN lookup is being requested
    contact_uuids = kwargs.get("c", None)  # contacts with ids
    group_uuids = kwargs.get("g", None)  # groups with ids
    search = kwargs.get("search", None)  # search of groups, contacts and URNs
    types = list(kwargs.get("types", ""))  # limit search to types (g | s | c)

    if contact_uuids:
        return org.contacts.filter(
            uuid__in=contact_uuids.split(","), status=Contact.STATUS_ACTIVE, is_active=True
        ).order_by(Lower("name"))
    elif group_uuids:
        return ContactGroup.get_groups(org).filter(uuid__in=group_uuids.split(",")).order_by(Lower("name"))

    # searching returns something which acts enough like a queryset to be paged
    return _omnibox_mixed_search(org, search, types)


def _omnibox_mixed_search(org, query: str, types: str):
    """
    Performs a mixed group and contact search, returning the first N matches of each type.
    """

    search_types = types or (SEARCH_ALL_GROUPS, SEARCH_CONTACTS)
    per_type_limit = 25
    results = []

    if SEARCH_ALL_GROUPS in search_types or SEARCH_STATIC_GROUPS in search_types:
        groups = ContactGroup.get_groups(org, ready_only=True)
        query_terms = query.split(" ") if query else ()

        # exclude dynamic groups if not searching all groups
        if SEARCH_ALL_GROUPS not in search_types:
            groups = groups.filter(query=None)

        for query_term in query_terms:
            groups = groups.filter(name__icontains=query_term)

        results += list(groups.order_by(Lower("name"))[:per_type_limit])

    if SEARCH_CONTACTS in search_types:
        if query:
            search_query = f"name ~ {json.dumps(query)}"
            if not org.is_anon:
                search_query += f" OR urn ~ {json.dumps(query)}"
        else:
            search_query = ""

        try:
            search_results = mailroom.get_client().contact_search(
                org, org.active_contacts_group, search_query, sort="name", limit=per_type_limit
            )
        except mailroom.QueryValidationException:
            return results

        contacts = IDSliceQuerySet(
            Contact,
            search_results.contact_ids,
            offset=0,
            total=len(search_results.contact_ids),
            only=("id", "uuid", "name", "org_id"),
        ).prefetch_related("org")

        Contact.bulk_urn_cache_initialize(contacts=results)
        results += list(contacts)

    return results


def omnibox_serialize(org, groups, contacts, *, json_encode=False):
    """
    Shortcut for proper way to serialize a queryset of groups and contacts for omnibox component
    """
    serialized = omnibox_results_to_dict(org, list(groups) + list(contacts))

    if json_encode:
        return [json.dumps(_) for _ in serialized]

    return serialized


def omnibox_deserialize(org, omnibox):
    group_ids = [item["id"] for item in omnibox if item["type"] == "group"]
    contact_ids = [item["id"] for item in omnibox if item["type"] == "contact"]

    return {
        "groups": org.groups.filter(uuid__in=group_ids, is_active=True),
        "contacts": Contact.objects.filter(uuid__in=contact_ids, org=org, is_active=True),
    }


def omnibox_results_to_dict(org, results):
    """
    Converts the result of a omnibox query (queryset of contacts, groups or URNs, or a list) into a dict {id, text}
    """
    formatted = []

    groups = [r for r in results if isinstance(r, ContactGroup)]
    group_counts = ContactGroupCount.get_totals(groups) if groups else {}

    for obj in results:
        if isinstance(obj, ContactGroup):
            result = {"id": str(obj.uuid), "name": obj.name, "type": "group", "count": group_counts[obj]}
        elif isinstance(obj, Contact):
            if org.is_anon:
                result = {"id": str(obj.uuid), "name": obj.get_display(org), "type": "contact"}
            else:
                result = {
                    "id": str(obj.uuid),
                    "name": obj.get_display(org),
                    "type": "contact",
                    "urn": obj.get_urn_display(),
                }

        formatted.append(result)

    return formatted
