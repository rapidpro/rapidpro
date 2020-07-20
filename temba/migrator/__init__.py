from django.db import connections
from django.conf import settings


SELECT_LIMIT = 1000


class MigratorObject:
    def __init__(self, **entries):

        # Generic
        self.count = None

        # Org fields
        self.id = None
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

    def get_count(self, table_name, condition=None):
        if condition:
            condition = f"WHERE {condition}"

        query = self.make_query_one(query_string=f"SELECT count(*) as count FROM public.{table_name} {condition}")
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
        orgs_count = self.get_count("orgs_org", condition="is_active = true")
        results = self.get_results_paginated(
            query_string="SELECT * FROM public.orgs_org WHERE is_active = true ORDER BY name ASC", count=orgs_count
        )
        return results

    def get_org(self) -> MigratorObject:
        return self.make_query_one(query_string=f"SELECT * FROM public.orgs_org WHERE id = {self.org_id}")

    def get_org_topups(self) -> (list, int):
        topups_count = self.get_count("orgs_topup", condition=f"org_id = {self.org_id} AND is_active = true")
        return (
            self.get_results_paginated(
                query_string=f"SELECT * FROM public.orgs_topup WHERE org_id = {self.org_id} AND is_active = true ORDER BY id ASC",
                count=topups_count,
            ),
            topups_count,
        )

    def get_org_topups_credit(self, topup_id) -> list:
        topupcredits_count = self.get_count("orgs_topupcredits", condition=f"topup_id = {topup_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.orgs_topupcredits WHERE topup_id = {topup_id} ORDER BY id ASC",
            count=topupcredits_count,
        )

    def get_org_languages(self) -> (list, int):
        languages_count = self.get_count("orgs_language", condition=f"org_id = {self.org_id}")
        return (
            self.get_results_paginated(
                query_string=f"SELECT * FROM public.orgs_language WHERE org_id = {self.org_id} ORDER BY id ASC",
                count=languages_count,
            ),
            languages_count,
        )

    def get_org_channels(self) -> (list, int):
        channels_count = self.get_count("channels_channel", condition=f"org_id = {self.org_id} AND is_active = true")
        return (
            self.get_results_paginated(
                query_string=f"SELECT * FROM public.channels_channel WHERE org_id = {self.org_id} AND is_active = true ORDER BY id ASC",
                count=channels_count,
            ),
            channels_count,
        )

    def get_channels_count(self, channel_id) -> list:
        channels_count = self.get_count("channels_channelcount", condition=f"channel_id = {channel_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.channels_channelcount WHERE channel_id = {channel_id} ORDER BY id ASC",
            count=channels_count,
        )

    def get_channel_syncevents(self, channel_id) -> list:
        syncevents_count = self.get_count("channels_syncevent", condition=f"channel_id = {channel_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.channels_syncevent WHERE channel_id = {channel_id} ORDER BY id ASC",
            count=syncevents_count,
        )

    def get_channel_logs(self, channel_id) -> (list, int):
        count = self.get_count("channels_channellog", condition=f"channel_id = {channel_id}")
        return (
            self.get_results_paginated(
                query_string=f"SELECT * FROM public.channels_channellog WHERE channel_id = {channel_id} ORDER BY id ASC",
                count=count,
            ),
            count,
        )

    def get_org_contact_fields(self) -> (list, int):
        count = self.get_count("contacts_contactfield", condition=f"org_id = {self.org_id} AND is_active = true")
        return (
            self.get_results_paginated(
                query_string=f"SELECT * FROM public.contacts_contactfield WHERE org_id = {self.org_id} AND is_active = true ORDER BY id ASC",
                count=count,
            ),
            count,
        )

    def get_org_contacts(self) -> (list, int):
        count = self.get_count(
            "contacts_contact", condition=f"org_id = {self.org_id} AND is_test = false AND is_active = true"
        )
        return (
            self.get_results_paginated(
                query_string=f"SELECT * FROM public.contacts_contact WHERE org_id = {self.org_id} AND is_test = false  AND is_active = true ORDER BY id ASC",
                count=count,
            ),
            count,
        )

    def get_values_value(self, contact_id) -> list:
        count = self.get_count(
            "values_value",
            condition=f"org_id = {self.org_id} AND contact_id = {contact_id} AND contact_field_id IS NOT NULL",
        )
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.values_value WHERE org_id = {self.org_id} AND contact_id = {contact_id} AND contact_field_id IS NOT NULL ORDER BY id ASC",
            count=count,
        )

    def get_contact_urns(self, contact_id) -> list:
        count = self.get_count(
            "contacts_contacturn", condition=f"org_id = {self.org_id} AND contact_id = {contact_id}"
        )
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.contacts_contacturn WHERE org_id = {self.org_id} AND contact_id = {contact_id} ORDER BY id ASC",
            count=count,
        )

    def get_org_contact_groups(self) -> (list, int):
        count = self.get_count(
            "contacts_contactgroup", condition=f"org_id = {self.org_id} AND group_type = 'U' AND is_active = true"
        )
        return (
            self.get_results_paginated(
                query_string=f"SELECT * FROM public.contacts_contactgroup WHERE org_id = {self.org_id} AND group_type = 'U' AND is_active = true ORDER BY id ASC",
                count=count,
            ),
            count,
        )

    def get_contactgroups_contacts(self, contactgroup_id) -> list:
        count = self.get_count("contacts_contactgroup_contacts", condition=f"contactgroup_id = {contactgroup_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.contacts_contactgroup_contacts WHERE contactgroup_id = {contactgroup_id} ORDER BY id ASC",
            count=count,
        )

    def get_org_channel_events(self) -> (list, int):
        count = self.get_count("channels_channelevent", condition=f"org_id = {self.org_id}")
        return (
            self.get_results_paginated(
                query_string=f"SELECT * FROM public.channels_channelevent WHERE org_id = {self.org_id} ORDER BY id ASC",
                count=count,
            ),
            count,
        )

    def get_org_trigger_schedules(self) -> (list, int):
        count_query = self.make_query_one(
            query_string=f"SELECT count(ss.*) as count FROM public.schedules_schedule ss INNER JOIN "
            f"public.triggers_trigger tt ON (ss.id = tt.schedule_id) WHERE tt.org_id = {self.org_id} "
            f"AND tt.schedule_id IS NOT NULL"
        )
        return (
            self.get_results_paginated(
                query_string=f"SELECT ss.* as count FROM public.schedules_schedule ss INNER JOIN "
                f"public.triggers_trigger tt ON (ss.id = tt.schedule_id) WHERE tt.org_id = {self.org_id} "
                f"AND tt.schedule_id IS NOT NULL",
                count=count_query.count,
            ),
            count_query.count,
        )

    def get_org_broadcast_schedules(self) -> (list, int):
        count_query = self.make_query_one(
            query_string=f"SELECT count(ss.*) as count FROM public.schedules_schedule ss INNER JOIN "
            f"public.msgs_broadcast mb ON (ss.id = mb.schedule_id) WHERE mb.org_id = {self.org_id} "
            f"AND mb.schedule_id IS NOT NULL"
        )
        return (
            self.get_results_paginated(
                query_string=f"SELECT ss.* FROM public.schedules_schedule ss INNER JOIN "
                f"public.msgs_broadcast mb ON (ss.id = mb.schedule_id) WHERE mb.org_id = {self.org_id} "
                f"AND mb.schedule_id IS NOT NULL",
                count=count_query.count,
            ),
            count_query.count,
        )

    def get_org_msg_broadcasts(self) -> (list, int):
        count = self.get_count("msgs_broadcast", condition=f"org_id = {self.org_id}")
        return (
            self.get_results_paginated(
                query_string=f"SELECT * FROM public.msgs_broadcast WHERE org_id = {self.org_id} ORDER BY id ASC",
                count=count,
            ),
            count,
        )

    def get_msg_broadcast_contacts(self, broadcast_id) -> list:
        count = self.get_count("msgs_broadcast_contacts", condition=f"broadcast_id = {broadcast_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.msgs_broadcast_contacts WHERE broadcast_id = {broadcast_id} ORDER BY id ASC",
            count=count,
        )

    def get_msg_broadcast_groups(self, broadcast_id) -> list:
        count = self.get_count("msgs_broadcast_groups", condition=f"broadcast_id = {broadcast_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.msgs_broadcast_groups WHERE broadcast_id = {broadcast_id} ORDER BY id ASC",
            count=count,
        )

    def get_msg_broadcast_urns(self, broadcast_id) -> list:
        count = self.get_count("msgs_broadcast_urns", condition=f"broadcast_id = {broadcast_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.msgs_broadcast_urns WHERE broadcast_id = {broadcast_id} ORDER BY id ASC",
            count=count,
        )

    def get_org_msg_labels(self, label_type) -> (list, int):
        count = self.get_count(
            "msgs_label", condition=f"org_id = {self.org_id} AND is_active = true AND label_type = '{label_type}'"
        )
        return (
            self.get_results_paginated(
                query_string=f"SELECT * FROM public.msgs_label WHERE org_id = {self.org_id} AND is_active = true AND label_type = '{label_type}' ORDER BY id ASC",
                count=count,
            ),
            count,
        )

    def get_org_msgs(self) -> (list, int):
        count = self.get_count("msgs_msg", condition=f"org_id = {self.org_id}")
        return (
            self.get_results_paginated(
                query_string=f"SELECT * FROM public.msgs_msg WHERE org_id = {self.org_id} ORDER BY id ASC", count=count
            ),
            count,
        )

    def get_msg_labels(self, msg_id) -> list:
        count = self.get_count("msgs_msg_labels", condition=f"msg_id = {msg_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.msgs_msg_labels WHERE msg_id = {msg_id} ORDER BY id ASC", count=count
        )

    def get_org_flow_labels(self) -> (list, int):
        count = self.get_count("flows_flowlabel", condition=f"org_id = {self.org_id}")
        return (
            self.get_results_paginated(
                query_string=f"SELECT * FROM public.flows_flowlabel WHERE org_id = {self.org_id} ORDER BY id ASC",
                count=count,
            ),
            count,
        )

    def get_org_flows(self) -> (list, int):
        count = self.get_count(
            "flows_flow", condition=f"org_id = {self.org_id} AND is_archived = false AND is_active = true"
        )
        return (
            self.get_results_paginated(
                query_string=f"SELECT * FROM public.flows_flow WHERE org_id = {self.org_id} AND is_archived = false AND is_active = true ORDER BY id ASC",
                count=count,
            ),
            count,
        )

    def get_flow_fields_dependencies(self, flow_id) -> list:
        count = self.get_count("flows_flow_field_dependencies", condition=f"flow_id = {flow_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.flows_flow_field_dependencies WHERE flow_id = {flow_id} ORDER BY id ASC",
            count=count,
        )

    def get_flow_flow_dependencies(self, flow_id) -> list:
        count = self.get_count("flows_flow_flow_dependencies", condition=f"from_flow_id = {flow_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.flows_flow_flow_dependencies WHERE from_flow_id = {flow_id} ORDER BY id ASC",
            count=count,
        )

    def get_flow_group_dependencies(self, flow_id) -> list:
        count = self.get_count("flows_flow_group_dependencies", condition=f"flow_id = {flow_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.flows_flow_group_dependencies WHERE flow_id = {flow_id} ORDER BY id ASC",
            count=count,
        )

    def get_flow_labels(self, flow_id) -> list:
        count = self.get_count("flows_flow_labels", condition=f"flow_id = {flow_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.flows_flow_labels WHERE flow_id = {flow_id} ORDER BY id ASC",
            count=count,
        )

    def get_flow_category_count(self, flow_id) -> list:
        count = self.get_count("flows_flowcategorycount", condition=f"flow_id = {flow_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.flows_flowcategorycount WHERE flow_id = {flow_id} ORDER BY id ASC",
            count=count,
        )

    def get_flow_node_count(self, flow_id) -> list:
        count = self.get_count("flows_flownodecount", condition=f"flow_id = {flow_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.flows_flownodecount WHERE flow_id = {flow_id} ORDER BY id ASC",
            count=count,
        )

    def get_flow_path_count(self, flow_id) -> list:
        count = self.get_count("flows_flowpathcount", condition=f"flow_id = {flow_id} AND to_uuid IS NOT NULL")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.flows_flowpathcount WHERE flow_id = {flow_id} AND to_uuid IS NOT NULL ORDER BY id ASC",
            count=count,
        )

    def get_flow_actionsets(self, flow_id) -> list:
        count = self.get_count("flows_actionset", condition=f"flow_id = {flow_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.flows_actionset WHERE flow_id = {flow_id} ORDER BY id ASC", count=count
        )

    def get_flow_rulesets(self, flow_id) -> list:
        count = self.get_count("flows_ruleset", condition=f"flow_id = {flow_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.flows_ruleset WHERE flow_id = {flow_id} ORDER BY id ASC", count=count
        )

    def get_flow_revisions(self, flow_id) -> list:
        count = self.get_count("flows_flowrevision", condition=f"flow_id = {flow_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.flows_flowrevision WHERE flow_id = {flow_id} ORDER BY id ASC",
            count=count,
        )

    def get_flow_images(self, flow_id) -> list:
        count = self.get_count("flows_flowimage", condition=f"flow_id = {flow_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.flows_flowimage WHERE flow_id = {flow_id} ORDER BY id ASC", count=count
        )

    def get_flow_starts(self, flow_id) -> list:
        count = self.get_count("flows_flowstart", condition=f"flow_id = {flow_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.flows_flowstart WHERE flow_id = {flow_id} ORDER BY id ASC", count=count
        )

    def get_flow_start_contacts(self, flowstart_id) -> list:
        count = self.get_count("flows_flowstart_contacts", condition=f"flowstart_id = {flowstart_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.flows_flowstart_contacts WHERE flowstart_id = {flowstart_id} ORDER BY id ASC",
            count=count,
        )

    def get_flow_start_groups(self, flowstart_id) -> list:
        count = self.get_count("flows_flowstart_groups", condition=f"flowstart_id = {flowstart_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.flows_flowstart_groups WHERE flowstart_id = {flowstart_id} ORDER BY id ASC",
            count=count,
        )

    def get_flow_runs(self, flow_id) -> list:
        count = self.get_count("flows_flowrun", condition=f"flow_id = {flow_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.flows_flowrun WHERE flow_id = {flow_id} ORDER BY id ASC", count=count
        )

    def get_org_resthooks(self) -> (list, int):
        count = self.get_count("api_resthook", condition=f"org_id = {self.org_id} AND is_active = true")
        return (
            self.get_results_paginated(
                query_string=f"SELECT * FROM public.api_resthook WHERE org_id = {self.org_id} AND is_active = true ORDER BY id ASC",
                count=count,
            ),
            count,
        )

    def get_resthook_subscribers(self, resthook_id) -> list:
        count = self.get_count("api_resthooksubscriber", condition=f"resthook_id = {resthook_id} AND is_active = true")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.api_resthook WHERE resthook_id = {resthook_id} AND is_active = true ORDER BY id ASC",
            count=count,
        )

    def get_org_webhook_events(self) -> (list, int):
        count = self.get_count("api_webhookevent", condition=f"org_id = {self.org_id} AND is_active = true")
        return (
            self.get_results_paginated(
                query_string=f"SELECT * FROM public.api_webhookevent WHERE org_id = {self.org_id} AND is_active = true ORDER BY id ASC",
                count=count,
            ),
            count,
        )

    def get_webhook_event_results(self, event_id) -> list:
        count = self.get_count("api_webhookresult", condition=f"event_id = {event_id} AND is_active = true")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.api_webhookresult WHERE event_id = {event_id} AND is_active = true ORDER BY id ASC",
            count=count,
        )

    def get_org_campaigns(self) -> (list, int):
        count = self.get_count(
            "campaigns_campaign", condition=f"org_id = {self.org_id} AND is_archived = false AND is_active = true"
        )
        return (
            self.get_results_paginated(
                query_string=f"SELECT * FROM public.campaigns_campaign WHERE org_id = {self.org_id} AND is_archived = false AND is_active = true ORDER BY id ASC",
                count=count,
            ),
            count,
        )

    def get_campaign_events(self, campaign_id) -> list:
        count = self.get_count("campaigns_campaignevent", condition=f"campaign_id = {campaign_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.campaigns_campaignevent WHERE campaign_id = {campaign_id} ORDER BY id ASC",
            count=count,
        )

    def get_event_fires(self, event_id) -> list:
        count = self.get_count("campaigns_eventfire", condition=f"event_id = {event_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.campaigns_eventfire WHERE event_id = {event_id} ORDER BY id ASC",
            count=count,
        )

    def get_org_triggers(self) -> (list, int):
        count = self.get_count(
            "triggers_trigger", condition=f"org_id = {self.org_id} AND is_archived = false AND is_active = true"
        )
        return (
            self.get_results_paginated(
                query_string=f"SELECT * FROM public.triggers_trigger WHERE org_id = {self.org_id} AND is_archived = false AND is_active = true ORDER BY id ASC",
                count=count,
            ),
            count,
        )

    def get_trigger_contacts(self, trigger_id) -> list:
        count = self.get_count("triggers_trigger_contacts", condition=f"trigger_id = {trigger_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.triggers_trigger_contacts WHERE trigger_id = {trigger_id} ORDER BY id ASC",
            count=count,
        )

    def get_trigger_groups(self, trigger_id) -> list:
        count = self.get_count("triggers_trigger_groups", condition=f"trigger_id = {trigger_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.triggers_trigger_groups WHERE trigger_id = {trigger_id} ORDER BY id ASC",
            count=count,
        )

    def get_org_links(self) -> (list, int):
        count = self.get_count(
            "links_link", condition=f"org_id = {self.org_id} AND is_archived = false AND is_active = true"
        )
        return (
            self.get_results_paginated(
                query_string=f"SELECT * FROM public.links_link WHERE org_id = {self.org_id} AND is_archived = false AND is_active = true ORDER BY id ASC",
                count=count,
            ),
            count,
        )

    def get_link_contacts(self, link_id) -> list:
        count = self.get_count("links_linkcontacts", condition=f"link_id = {link_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.links_linkcontacts WHERE link_id = {link_id} ORDER BY id ASC",
            count=count,
        )
