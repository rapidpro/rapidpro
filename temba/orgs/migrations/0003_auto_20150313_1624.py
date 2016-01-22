# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, connection

class Migration(migrations.Migration):

    def calculate_used(apps, schema_editor):
        """
        Iterate across all our topups, calculate how many messages are assigned to them
        """
        TopUp = apps.get_model('orgs', 'TopUp')
        Msg = apps.get_model('msgs', 'Msg')
        for topup in TopUp.objects.all():
            topup.used = Msg.all_messages.filter(topup=topup).count()
            topup.save()

    def install_topup_used_trigger(apps, schema_editor):
        """
        Installs a Postgres trigger that will update the # of used credits in a topup when
        a new Msg is created.
        """
        #language=SQL
        install_trigger = """
            CREATE OR REPLACE FUNCTION update_topup_used() RETURNS TRIGGER AS $$
            BEGIN
              -- Msg is being created
              IF TG_OP = 'INSERT' THEN
                -- If we have a topup, increment our # of used credits
                IF NEW.topup_id IS NOT NULL THEN
                  UPDATE orgs_topup SET used=used+1 where id=NEW.topup_id;
                END IF;

              -- Msg is being updated
              ELSIF TG_OP = 'UPDATE' THEN
                -- If the topup has changed
                IF NEW.topup_id IS DISTINCT FROM OLD.topup_id THEN
                  -- If our old topup wasn't null then decrement our used credits on it
                  IF OLD.topup_id IS NOT NULL THEN
                    UPDATE orgs_topup SET used=used-1 where id=OLD.topup_id;
                  END IF;

                  -- if our new topup isn't null, then increment our used credits on it
                  IF NEW.topup_id IS NOT NULL THEN
                    UPDATE orgs_topup SET used=used+1 where id=NEW.topup_id;
                  END IF;
                END IF;

              -- Msg is being deleted
              ELSIF TG_OP = 'DELETE' THEN
                -- Remove a used credit if this Msg had one assigned
                IF OLD.topup_id IS NOT NULL THEN
                  UPDATE orgs_topup SET used=used-1 WHERE id=OLD.topup_id;
                END IF;

              -- Msgs table is being truncated
              ELSIF TG_OP = 'TRUNCATE' THEN
                -- Clear all used credits
                UPDATE orgs_topup SET used=0;

              END IF;

              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            DROP TRIGGER IF EXISTS when_msgs_update_then_update_topup_trg on msgs_msg;
            CREATE TRIGGER when_msgs_update_then_update_topup_trg
               AFTER INSERT OR DELETE OR UPDATE OF topup_id
               ON msgs_msg
               FOR EACH ROW
               EXECUTE PROCEDURE update_topup_used();

            DROP TRIGGER IF EXISTS when_msgs_truncate_then_update_topup_trg on msgs_msg;
            CREATE TRIGGER when_msgs_truncate_then_update_topup_trg
              AFTER TRUNCATE
              ON msgs_msg
              EXECUTE PROCEDURE update_topup_used();
        """
        cursor = connection.cursor()
        cursor.execute(install_trigger)

    dependencies = [
        ('msgs', '0003_auto_20150129_0515'),
        ('orgs', '0002_topup_used'),
    ]

    operations = [
        migrations.RunPython(
            calculate_used,
        ),
        migrations.RunPython(
            install_topup_used_trigger,
        ),
    ]
