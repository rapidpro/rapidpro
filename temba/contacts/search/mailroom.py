from temba import mailroom


def parse_query(org, query: str, *, parse_only: bool = False) -> mailroom.ParsedQuery:
    """
    Parses the passed in query in the context of the org
    """

    return mailroom.get_client().parse_query(org.id, query, parse_only=parse_only)


def search_contacts(
    org, query: str, *, group=None, sort: str = None, offset: int = None, exclude_ids=()
) -> mailroom.SearchResults:
    group_id = group.id if group else None

    return mailroom.get_client().contact_search(
        org.id, group_id=group_id, query=query, sort=sort, offset=offset, exclude_ids=exclude_ids
    )
