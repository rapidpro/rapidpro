from django.db import connections, connection
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

        # Msg
        self.channel_id = None
        self.contact_id = None
        self.broadcast_id = None
        self.topup_id = None
        self.contact_urn_id = None
        self.response_to_id = None

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

    def make_query_one_local(self, query_string) -> MigratorObject:
        cursor = connection.cursor()
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

    def get_org_channels(self, start_date=None, end_date=None) -> (list, int):
        condition_string = f"""
            org_id = {self.org_id} {"AND (created_on >= '%s' AND created_on <= '%s')" % (start_date, end_date) if start_date else ""}
        """
        channels_count = self.get_count("channels_channel", condition=condition_string)
        query_string = f"SELECT * FROM public.channels_channel WHERE {condition_string} ORDER BY id ASC"
        return self.get_results_paginated(query_string=query_string, count=channels_count), channels_count

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

    def get_org_contact_fields(self, start_date=None, end_date=None) -> (list, int):
        condition_string = f"""
            org_id = {self.org_id} AND is_active = true {"AND (created_on >= '%s' AND created_on <= '%s')" % (start_date, end_date) if start_date else ""}
        """
        query_string = f"SELECT * FROM public.contacts_contactfield WHERE {condition_string} ORDER BY id ASC"
        count = self.get_count("contacts_contactfield", condition=condition_string)
        return self.get_results_paginated(query_string=query_string, count=count), count

    def get_org_contacts(self, start_date=None, end_date=None) -> (list, int):
        condition_string = f"""
            org_id = {self.org_id} AND is_test = false AND is_active = true 
            {"AND (created_on >= '%s' AND created_on <= '%s')" % (start_date, end_date) if start_date else ""}
        """
        query_string = f"SELECT * FROM public.contacts_contact WHERE {condition_string} ORDER BY id ASC"
        count = self.get_count("contacts_contact", condition=condition_string)
        return self.get_results_paginated(query_string=query_string, count=count), count

    def get_values_value(self, contact_id) -> list:
        condition_string = f"org_id = {self.org_id} AND contact_id = {contact_id} AND contact_field_id IS NOT NULL"
        query_string = f"SELECT * FROM public.values_value WHERE {condition_string} ORDER BY id ASC"
        count = self.get_count("values_value", condition=condition_string)
        return self.get_results_paginated(query_string=query_string, count=count)

    def get_contact_urns(self, contact_id) -> list:
        count = self.get_count(
            "contacts_contacturn", condition=f"org_id = {self.org_id} AND contact_id = {contact_id}"
        )
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.contacts_contacturn WHERE org_id = {self.org_id} AND contact_id = {contact_id} ORDER BY id ASC",
            count=count,
        )

    def get_org_contact_groups(self, start_date=None, end_date=None) -> (list, int):
        condition_string = f"""
            org_id = {self.org_id} AND group_type = 'U' AND is_active = true 
            {"AND (created_on >= '%s' AND created_on <= '%s')" % (start_date, end_date) if start_date else ""}
        """
        query_string = f"SELECT * FROM public.contacts_contactgroup WHERE {condition_string} ORDER BY id ASC"
        count = self.get_count("contacts_contactgroup", condition=condition_string)
        return self.get_results_paginated(query_string=query_string, count=count), count

    def get_contactgroups_contacts(self, contactgroup_id) -> list:
        count = self.get_count("contacts_contactgroup_contacts", condition=f"contactgroup_id = {contactgroup_id}")
        return self.get_results_paginated(
            query_string=f"SELECT * FROM public.contacts_contactgroup_contacts WHERE contactgroup_id = {contactgroup_id} ORDER BY id ASC",
            count=count,
        )

    def get_org_channel_events(self, start_date=None, end_date=None) -> (list, int):
        condition_string = f"""
            org_id = {self.org_id} 
            {"AND (created_on >= '%s' AND created_on <= '%s')" % (start_date, end_date) if start_date else ""}
        """
        query_string = f"SELECT * FROM public.channels_channelevent WHERE {condition_string} ORDER BY id ASC"
        count = self.get_count("channels_channelevent", condition=condition_string)
        return self.get_results_paginated(query_string=query_string, count=count), count

    def get_org_trigger_schedules(self, start_date=None, end_date=None) -> (list, int):
        count_query_string = f"""
            SELECT count(ss.*) as count FROM public.schedules_schedule ss INNER JOIN 
            public.triggers_trigger tt ON (ss.id = tt.schedule_id) WHERE tt.org_id = {self.org_id} 
            AND tt.schedule_id IS NOT NULL 
            {"AND (ss.created_on >= '%s' AND ss.created_on <= '%s')" % (start_date, end_date) if start_date else ""}
        """
        query_string = f"""
            SELECT ss.* as count FROM public.schedules_schedule ss INNER JOIN 
            public.triggers_trigger tt ON (ss.id = tt.schedule_id) WHERE tt.org_id = {self.org_id} 
            AND tt.schedule_id IS NOT NULL
            {"AND (ss.created_on >= '%s' AND ss.created_on <= '%s')" % (start_date, end_date) if start_date else ""}
        """
        count_query = self.make_query_one(query_string=count_query_string)
        return self.get_results_paginated(query_string=query_string, count=count_query.count), count_query.count

    def get_org_broadcast_schedules(self, start_date=None, end_date=None) -> (list, int):
        count_query_string = f"""
            SELECT count(ss.*) as count FROM public.schedules_schedule ss INNER JOIN 
            public.msgs_broadcast mb ON (ss.id = mb.schedule_id) WHERE mb.org_id = {self.org_id} 
            AND mb.schedule_id IS NOT NULL
            {"AND (ss.created_on >= '%s' AND ss.created_on <= '%s')" % (start_date, end_date) if start_date else ""}
        """
        query_string = f"""
            SELECT ss.* FROM public.schedules_schedule ss INNER JOIN 
            public.msgs_broadcast mb ON (ss.id = mb.schedule_id) WHERE mb.org_id = {self.org_id} 
            AND mb.schedule_id IS NOT NULL
            {"AND (ss.created_on >= '%s' AND ss.created_on <= '%s')" % (start_date, end_date) if start_date else ""}
        """
        count_query = self.make_query_one(query_string=count_query_string)
        return self.get_results_paginated(query_string=query_string, count=count_query.count), count_query.count

    def get_org_msg_broadcasts(self) -> (list, int):
        count = self.get_count("msgs_broadcast", condition=f"org_id = {self.org_id} AND status not in ('P', 'I')")
        return (
            self.get_results_paginated(
                query_string=f"SELECT * FROM public.msgs_broadcast WHERE org_id = {self.org_id} AND status not in ('P', 'I') ORDER BY id ASC",
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
        count = self.get_count("flows_flow", condition=f"org_id = {self.org_id} AND is_archived = false")
        return (
            self.get_results_paginated(
                query_string=f"SELECT * FROM public.flows_flow WHERE org_id = {self.org_id} AND is_archived = false ORDER BY id ASC",
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

    def get_flow_run_events(self, flow_run_id) -> list:
        count_query = self.make_query_one(
            query_string=f"SELECT count(ffs.*) FROM public.flows_flowstep as ffs INNER JOIN public.flows_flowrun as ffr "
            f"ON (ffs.run_id = ffr.id) INNER JOIN public.flows_flowstep_messages as ffsm "
            f"ON (ffs.id = ffsm.flowstep_id) INNER JOIN public.msgs_msg as mm "
            f"ON (ffsm.msg_id = mm.id) INNER JOIN public.contacts_contact as cc "
            f"ON (mm.contact_id = cc.id) INNER JOIN public.contacts_contacturn as ccu "
            f"ON (cc.id = ccu.contact_id) INNER JOIN public.channels_channel as cch "
            f"ON (mm.channel_id = cch.id) WHERE ffr.id = {flow_run_id}"
        )
        return self.get_results_paginated(
            query_string=f"SELECT ffs.arrived_on, ffs.step_uuid, mm.uuid as msg_uuid, mm.text as msg_text, mm.direction as msg_direction, "
            f"cc.name as contact_name, cc.uuid as contact_uuid, ccu.path as urn_path, "
            f"ccu.scheme as urn_scheme, cch.uuid as channel_uuid, cch.name as channel_name "
            f"FROM public.flows_flowstep as ffs INNER JOIN public.flows_flowrun as ffr "
            f"ON (ffs.run_id = ffr.id) INNER JOIN public.flows_flowstep_messages as ffsm "
            f"ON (ffs.id = ffsm.flowstep_id) INNER JOIN public.msgs_msg as mm "
            f"ON (ffsm.msg_id = mm.id) INNER JOIN public.contacts_contact as cc "
            f"ON (mm.contact_id = cc.id) INNER JOIN public.contacts_contacturn as ccu "
            f"ON (cc.id = ccu.contact_id) INNER JOIN public.channels_channel as cch "
            f"ON (mm.channel_id = cch.id) WHERE ffr.id = {flow_run_id}",
            count=count_query.count,
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

    def get_msg_relationships(
        self, response_to_id, channel_id, contact_id, contact_urn_id, broadcast_id, topup_id, migration_task_id
    ):
        response_to_id = "null" if not response_to_id else response_to_id
        channel_id = "null" if not channel_id else channel_id
        contact_id = "null" if not contact_id else contact_id
        contact_urn_id = "null" if not contact_urn_id else contact_urn_id
        broadcast_id = "null" if not broadcast_id else broadcast_id
        topup_id = "null" if not topup_id else topup_id

        query_string = (
            f"SELECT new_id as contact_id,"
            f"(SELECT new_id from public.migrator_migrationassociation WHERE old_id = {channel_id} AND model = 'channels_channel' AND migration_task_id = {migration_task_id} ORDER BY id DESC LIMIT 1) as channel_id,"
            f"(SELECT new_id from public.migrator_migrationassociation WHERE old_id = {response_to_id} AND model = 'msgs_msg' AND migration_task_id = {migration_task_id} ORDER BY id DESC LIMIT 1) as response_to_id,"
            f"(SELECT new_id from public.migrator_migrationassociation WHERE old_id = {contact_urn_id} AND model = 'contacts_contacturn' AND migration_task_id = {migration_task_id} ORDER BY id DESC LIMIT 1) as contact_urn_id,"
            f"(SELECT new_id from public.migrator_migrationassociation WHERE old_id = {broadcast_id} AND model = 'msgs_broadcast' AND migration_task_id = {migration_task_id} ORDER BY id DESC LIMIT 1) as broadcast_id,"
            f"(SELECT new_id from public.migrator_migrationassociation WHERE old_id = {topup_id} AND model = 'orgs_topups' AND migration_task_id = {migration_task_id} ORDER BY id DESC LIMIT 1) as topup_id "
            f"FROM public.migrator_migrationassociation "
            f"WHERE old_id = {contact_id} AND model = 'contacts_contact' AND migration_task_id = {migration_task_id} "
            f"ORDER BY id DESC LIMIT 1"
        )

        obj = self.make_query_one_local(query_string=query_string)

        response_to_id = obj.response_to_id if hasattr(obj, "response_to_id") else None
        channel_id = obj.channel_id if hasattr(obj, "channel_id") else None
        contact_id = obj.contact_id if hasattr(obj, "contact_id") else None
        contact_urn_id = obj.contact_urn_id if hasattr(obj, "contact_urn_id") else None
        broadcast_id = obj.broadcast_id if hasattr(obj, "broadcast_id") else None
        topup_id = obj.topup_id if hasattr(obj, "topup_id") else None

        return response_to_id, channel_id, contact_id, contact_urn_id, broadcast_id, topup_id
