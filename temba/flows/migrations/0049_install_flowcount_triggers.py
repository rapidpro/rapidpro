# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations, connection
from django.db.models import Count

class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0048_auto_20160126_2305'),
    ]

    def clear_flowrun_counts(apps, schema_editor):
        """
        Clears all flowrun counts
        """
        FlowRunCount = apps.get_model('flows', 'FlowRunCount')
        FlowRunCount.objects.all().delete()

    def backfill_flowrun_counts(apps, schema_editor):
        """
        Backfills our counts for all flows
        """
        Flow = apps.get_model('flows', 'Flow')
        FlowRun = apps.get_model('flows', 'FlowRun')
        FlowRunCount = apps.get_model('flows', 'FlowRunCount')
        Contact = apps.get_model('contacts', 'Contact')

        # for each flow that has at least one run
        for flow in Flow.objects.exclude(runs=None):
            # get test contacts on this org
            test_contacts = Contact.objects.filter(org=flow.org, is_test=True).values('id')

            # calculate our count for each exit type
            counts = FlowRun.objects.filter(flow=flow).exclude(contact__in=test_contacts)\
                                    .values('exit_type').annotate(Count('exit_type'))

            # remove old ones
            FlowRunCount.objects.filter(flow=flow).delete()

            # insert updated counts for each
            for count in counts:
                if count['exit_type__count'] > 0:
                    FlowRunCount.objects.create(flow=flow, exit_type=count['exit_type'], count=count['exit_type__count'])

            print "%s - %s" % (flow.name, counts)

    def install_flowruncount_triggers(apps, schema_editor):
        """
        Installs a Postgres triggers to manage our flowrun counts.
        """
        #language=SQL
        install_trigger = """
            ----------------------------------------------------------------------
            -- Squashes the flowrun counts for a particular flow and exit type
            ----------------------------------------------------------------------
            CREATE OR REPLACE FUNCTION
              temba_squash_flowruncount(_flow_id INT, _exit_type CHAR(1))
            RETURNS VOID AS $$
            BEGIN
              IF _exit_type IS NULL THEN
                WITH removed as (DELETE FROM flows_flowruncount
                  WHERE "flow_id" = _flow_id AND "exit_type" IS NULL RETURNING "count")
                  INSERT INTO flows_flowruncount("flow_id", "exit_type", "count")
                  VALUES (_flow_id, _exit_type, GREATEST(0, (SELECT SUM("count") FROM removed)));
              ELSE
                WITH removed as (DELETE FROM flows_flowruncount
                  WHERE "flow_id" = _flow_id AND "exit_type" = _exit_type RETURNING "count")
                  INSERT INTO flows_flowruncount("flow_id", "exit_type", "count")
                  VALUES (_flow_id, _exit_type, GREATEST(0, (SELECT SUM("count") FROM removed)));
              END IF;
            END;
            $$ LANGUAGE plpgsql;

            ----------------------------------------------------------------------
            -- Inserts a new flowrun_count
            ----------------------------------------------------------------------
            CREATE OR REPLACE FUNCTION
              temba_insert_flowruncount(_flow_id INT, _exit_type CHAR(1), _count INT)
            RETURNS VOID AS $$
            BEGIN
              INSERT INTO flows_flowruncount("flow_id", "exit_type", "count")
              VALUES(_flow_id, _exit_type, _count);
            END;
            $$ LANGUAGE plpgsql;

            ----------------------------------------------------------------------
            -- Increments or decrements our counts for each exit type
            ----------------------------------------------------------------------
            CREATE OR REPLACE FUNCTION temba_update_flowruncount() RETURNS TRIGGER AS $$
            BEGIN
              -- Table being cleared, reset all counts
              IF TG_OP = 'TRUNCATE' THEN
                TRUNCATE flows_flowruncounts;
                RETURN NULL;
              END IF;

              -- FlowRun being added
              IF TG_OP = 'INSERT' THEN
                 -- Is this a test contact, ignore
                 IF temba_contact_is_test(NEW.contact_id) THEN
                   RETURN NULL;
                 END IF;

                -- Increment appropriate type
                PERFORM temba_insert_flowruncount(NEW.flow_id, NEW.exit_type, 1);

              -- FlowRun being removed
              ELSIF TG_OP = 'DELETE' THEN
                 -- Is this a test contact, ignore
                 IF temba_contact_is_test(OLD.contact_id) THEN
                   RETURN NULL;
                 END IF;

                PERFORM temba_insert_flowruncount(OLD.flow_id, OLD.exit_type, -1);

              -- Updating exit type
              ELSIF TG_OP = 'UPDATE' THEN
                 -- Is this a test contact, ignore
                 IF temba_contact_is_test(NEW.contact_id) THEN
                   RETURN NULL;
                 END IF;

                PERFORM temba_insert_flowruncount(OLD.flow_id, OLD.exit_type, -1);
                PERFORM temba_insert_flowruncount(NEW.flow_id, NEW.exit_type, 1);
              END IF;

              RETURN NULL;
            END;
            $$ LANGUAGE plpgsql;

            -- Install INSERT, UPDATE and DELETE triggers
            DROP TRIGGER IF EXISTS temba_flowrun_update_flowruncount on flows_flowrun;
            CREATE TRIGGER temba_flowrun_update_flowruncount
               AFTER INSERT OR DELETE OR UPDATE OF exit_type
               ON flows_flowrun
               FOR EACH ROW
               EXECUTE PROCEDURE temba_update_flowruncount();

            -- Install TRUNCATE trigger
            DROP TRIGGER IF EXISTS temba_flowrun_truncate_flowruncount on flows_flowrun;
            CREATE TRIGGER temba_flowrun_truncate_flowruncount
              AFTER TRUNCATE
              ON flows_flowrun
              EXECUTE PROCEDURE temba_update_flowruncount();
        """
        cursor = connection.cursor()
        cursor.execute(install_trigger)

    def uninstall_flowruncount_triggers(apps, schema_editor):
        cursor = connection.cursor()
        #language=SQL
        cursor.execute("""
        DROP TRIGGER IF EXISTS temba_flowrun_update_flowruncount on flows_flowrun;
        DROP TRIGGER IF EXISTS temba_flowrun_truncate_flowruncount on flows_flowrun;
        """)

    operations = [
        migrations.RunPython(
            install_flowruncount_triggers, uninstall_flowruncount_triggers
        ),
        migrations.RunPython(
                backfill_flowrun_counts, clear_flowrun_counts
        )
    ]