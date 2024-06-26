from temba import mailroom


def parse_query(org, query: str, *, parse_only: bool = False) -> mailroom.ParsedQuery:
    """
    Parses the passed in query in the context of the org
    """

    return mailroom.get_client().parse_query(org.id, query, parse_only=parse_only)
