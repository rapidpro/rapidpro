from django.db import connections
from django.conf import settings


SELECT_LIMIT = 1000


def dictfetchall(cursor):
    """
    Return all rows from a cursor as a dict
    """
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def dictfetchone(cursor):
    """
    Return all rows from a cursor as a dict
    """
    columns = [col[0] for col in cursor.description]
    row = cursor.fetchone()
    return dict(zip(columns, row)) if row else None


def make_query(query_string, many=True):
    with connections[settings.DB_MIGRATION].cursor() as cursor:
        cursor.execute(query_string)
        result = dictfetchall(cursor) if many else dictfetchone(cursor)
        return result


def get_count(table_name):
    query = make_query(query_string=f"SELECT count(*) as count FROM public.{table_name}", many=False)
    return query.get("count")


def get_results_paginated(query_string, count):
    pages = count / SELECT_LIMIT
    pages_count = int(pages)
    page_rest = pages - pages_count

    if page_rest > 0:
        pages_count += 1

    results = []
    for i in range(1, pages_count + 1):
        results += make_query(query_string=f"{query_string} LIMIT {SELECT_LIMIT} OFFSET {(i - 1) * SELECT_LIMIT}")

    return results


def get_all_orgs():
    orgs_count = get_count("orgs_org")
    results = get_results_paginated(query_string="SELECT * FROM public.orgs_org", count=orgs_count)
    return results


def get_org(org_id):
    return make_query(query_string="SELECT * FROM public.orgs_org WHERE id = %s" % org_id, many=False)
