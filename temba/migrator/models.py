from django.db import connections
from django.conf import settings


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


def get_all_orgs():
    return make_query(query_string="SELECT * FROM public.orgs_org")


def get_org(org_id):
    return make_query(query_string="SELECT * FROM public.orgs_org WHERE id = %s" % org_id, many=False)


def get_all_users():
    return make_query(query_string="SELECT * FROM public.auth_user", many=True)
