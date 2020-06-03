from django.db import connections
from django.conf import settings


SELECT_LIMIT = 1000


class MigratorObject:
    def __init__(self, **entries):

        # Generic
        self.count = None

        # Org fields
        self.name = None
        self.plan = None
        self.plan_start = None
        self.stripe_customer = None
        self.language = None
        self.timezone = None
        self.date_format = None
        self.config = None
        self.is_anon = None
        self.surveyor_password = None
        self.parent_id = None
        self.primary_language_id = None

        self.__dict__.update(entries)


class Migrator(object):
    org_id = None

    def __init__(self, org_id=None):
        self.org_id = org_id

    def to_obj(self, **args) -> MigratorObject:
        return MigratorObject(**args)

    def dictfetchall(self, cursor) -> list:
        """
        Return all rows from a cursor as a list of MigratorObjects
        """
        columns = [col[0] for col in cursor.description]
        return [self.to_obj(**dict(zip(columns, row))) for row in cursor.fetchall()]

    def dictfetchone(self, cursor) -> MigratorObject:
        """
        Return one row from a cursor as a MigratorObject
        """
        columns = [col[0] for col in cursor.description]
        row = cursor.fetchone()
        return self.to_obj(**dict(zip(columns, row))) if row else None

    def make_query(self, query_string) -> list:
        with connections[settings.DB_MIGRATION].cursor() as cursor:
            cursor.execute(query_string)
            return self.dictfetchall(cursor)

    def make_query_one(self, query_string) -> MigratorObject:
        with connections[settings.DB_MIGRATION].cursor() as cursor:
            cursor.execute(query_string)
            return self.dictfetchone(cursor)

    def get_count(self, table_name):
        query = self.make_query_one(query_string=f"SELECT count(*) as count FROM public.{table_name}")
        return query.count

    def get_results_paginated(self, query_string, count) -> list:
        pages = count / SELECT_LIMIT
        pages_count = int(pages)
        page_rest = pages - pages_count

        if page_rest > 0:
            pages_count += 1

        results = []
        for i in range(1, pages_count + 1):
            results += self.make_query(
                query_string=f"{query_string} LIMIT {SELECT_LIMIT} OFFSET {(i - 1) * SELECT_LIMIT}"
            )

        return results

    def get_all_orgs(self) -> list:
        orgs_count = self.get_count("orgs_org")
        results = self.get_results_paginated(
            query_string="SELECT * FROM public.orgs_org ORDER BY name ASC", count=orgs_count
        )
        return results

    def get_org(self) -> MigratorObject:
        return self.make_query_one(query_string=f"SELECT * FROM public.orgs_org WHERE id = {self.org_id}")

    def get_org_topups(self) -> list:
        topups_count = self.get_count("orgs_topup")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.orgs_topup WHERE org_id = {self.org_id} AND is_active = true ORDER BY id ASC",
            count=topups_count,
        )

    def get_org_topups_credit(self, topup_id) -> list:
        topupcredits_count = self.get_count("orgs_topupcredits")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.orgs_topupcredits WHERE topup_id = {topup_id} ORDER BY id ASC",
            count=topupcredits_count,
        )

    def get_org_languages(self) -> list:
        languages_count = self.get_count("orgs_language")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.orgs_language WHERE org_id = {self.org_id} ORDER BY id ASC",
            count=languages_count,
        )

    def get_org_channels(self) -> list:
        channels_count = self.get_count("channels_channel")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.channels_channel WHERE org_id = {self.org_id} AND is_active = true ORDER BY id ASC",
            count=channels_count,
        )

    def get_channels_count(self, channel_id) -> list:
        channels_count = self.get_count("channels_channelcount")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.channels_channelcount WHERE channel_id = {channel_id} ORDER BY id ASC",
            count=channels_count,
        )

    def get_channel_syncevents(self, channel_id) -> list:
        syncevents_count = self.get_count("channels_syncevent")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.channels_syncevent WHERE channel_id = {channel_id} ORDER BY id ASC",
            count=syncevents_count,
        )

    def get_org_contact_fields(self) -> list:
        count = self.get_count("contacts_contactfield")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.contacts_contactfield WHERE org_id = {self.org_id} AND is_active = true ORDER BY id ASC",
            count=count,
        )

    def get_org_contacts(self) -> list:
        count = self.get_count("contacts_contact")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.contacts_contact WHERE org_id = {self.org_id} AND is_test = false ORDER BY id ASC",
            count=count,
        )

    def get_org_contact_groups(self) -> list:
        count = self.get_count("contacts_contactgroup")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.contacts_contactgroup WHERE org_id = {self.org_id} AND group_type = 'U' ORDER BY id ASC",
            count=count,
        )
