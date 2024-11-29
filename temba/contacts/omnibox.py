import json

from django.db.models.functions import Lower

from temba import mailroom
from temba.utils.models.es import IDSliceQuerySet

from .models import Contact, ContactGroup, ContactGroupCount

SEARCH_ALL_GROUPS = "g"
SEARCH_STATIC_GROUPS = "s"
SEARCH_CONTACTS = "c"
PER_TYPE_LIMIT = 25


def omnibox_query(org, **kwargs) -> tuple:
    """
    Performs a omnibox query returning a tuple of groups and contacts.
    """

    contact_uuids = kwargs.get("c", None)  # contacts with ids
    group_uuids = kwargs.get("g", None)  # groups with ids
    search = kwargs.get("search", "")  # search of groups, contacts
    types = list(kwargs.get("types", ""))  # limit search to types (g | s | c)
    groups, contacts = (), ()

    if contact_uuids:
        contacts = org.contacts.filter(
            uuid__in=contact_uuids.split(","), status=Contact.STATUS_ACTIVE, is_active=True
        ).order_by(Lower("name"))
    elif group_uuids:
        groups = ContactGroup.get_groups(org).filter(uuid__in=group_uuids.split(",")).order_by(Lower("name"))
    else:
        groups, contacts = _mixed_search(org, search.strip(), types)

    Contact.bulk_urn_cache_initialize(contacts)

    return groups, contacts


def _mixed_search(org, search: str, types: str) -> tuple:
    """
    Performs a mixed group and contact search, returning the first 25 matches of each.
    """

    search_types = types or (SEARCH_ALL_GROUPS, SEARCH_CONTACTS)
    groups = []
    contacts = []

    if SEARCH_ALL_GROUPS in search_types or SEARCH_STATIC_GROUPS in search_types:
        groups = ContactGroup.get_groups(org, ready_only=True)

        # exclude dynamic groups if not searching all groups
        if SEARCH_ALL_GROUPS not in search_types:
            groups = groups.filter(query=None)

        if search:
            groups = groups.filter(name__icontains=search)

        groups = list(groups.order_by(Lower("name"))[:PER_TYPE_LIMIT])

    # listing contacts without any filtering isn't very useful for a typical workspaces and contactql requires name
    # terms be at least 2 chars and URN terms 3 chars
    if SEARCH_CONTACTS in search_types and len(search) >= 3:
        query = f"name ~ {json.dumps(search)}"
        if not org.is_anon:
            query += f" OR urn ~ {json.dumps(search)}"

        try:
            search_results = mailroom.get_client().contact_search(
                org, org.active_contacts_group, query, sort="name", limit=PER_TYPE_LIMIT
            )
            contacts = list(
                IDSliceQuerySet(
                    Contact,
                    search_results.contact_ids,
                    offset=0,
                    total=len(search_results.contact_ids),
                    only=("id", "uuid", "name", "org_id"),
                ).prefetch_related("org")
            )
        except mailroom.QueryValidationException:
            pass

    return groups, contacts


def omnibox_serialize(org, groups, contacts, *, encode=False):
    """
    Serializes lists of groups and contacts into the combined list format expected by the omnibox.
    """

    group_counts = ContactGroupCount.get_totals(groups) if groups else {}
    results = []

    for group in groups:
        results.append({"id": str(group.uuid), "name": group.name, "type": "group", "count": group_counts[group]})

    for contact in contacts:
        result = {"id": str(contact.uuid), "name": contact.get_display(org), "type": "contact"}

        if not org.is_anon:
            result["urn"] = contact.get_urn_display()

        results.append(result)

    # omniboxes submit as a repeating parameter for each item, so each item needs to be encoded as JSON but not the list
    # itself, e.g. recipients=%7B%22id%22%3A1%7D&recipients=%7B%22id%22%3A2%7D
    if encode:
        return [json.dumps(r) for r in results]

    return results


def omnibox_deserialize(org, results: list) -> tuple:
    """
    Deserializes the combined list format used by the omnibox into a tuple of groups and contacts.
    """

    group_uuids = [item["id"] for item in results if item["type"] == "group"]
    contact_uuids = [item["id"] for item in results if item["type"] == "contact"]

    return (
        org.groups.filter(uuid__in=group_uuids, is_active=True),
        org.contacts.filter(uuid__in=contact_uuids, is_active=True),
    )
