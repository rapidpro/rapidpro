# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, connection

class Migration(migrations.Migration):

    def calculate_used(apps, schema_editor):
        """
        Iterate across all our topups, calculate how many messages are assigned to them
        """
        from temba.orgs.models import TopUp
        for topup in TopUp.objects.all():
            topup.used = TopUp.msgs.all().count()
            topup.save()

    def install_topup_used_trigger(apps, schema_editor):
        """
        Installs a Postgres trigger that will update the # of used credits in a topup when
        a new Msg is created.
        """
        from temba.orgs.models import TopUp

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
                IF NEW.topup_id IS NULL AND OLD.topup_id IS NOT NULL OR
                   NEW.topup_id IS NOT NULL AND OLD.topup_id is NULL OR
                   NEW.topup_id <> OLD.topup_id THEN
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

              END IF;

              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            DROP TRIGGER IF EXISTS update_topup_used_trg ON msgs_msg;

            CREATE TRIGGER update_topup_used_trg
               AFTER INSERT OR DELETE OR UPDATE OF topup_id
               ON msgs_msg
               FOR EACH ROW
               EXECUTE PROCEDURE update_topup_used();
        """
        cursor = connection.cursor()
        cursor.execute(install_trigger)

    dependencies = [
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
