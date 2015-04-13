# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations, connection

class Migration(migrations.Migration):

    def calculate_counts(apps, schema_editor):
        """
        Iterate across all our groups, calculate how many contacts are in them
        """
        ContactGroup = apps.get_model('contacts', 'ContactGroup')
        for group in ContactGroup.objects.all():
            group.count = group.contacts.filter(is_test=False).count()
            group.save()

    def install_group_trigger(apps, schema_editor):
        """
        Installs a Postgres trigger that will increment or decrement our group count
        based on inclusion in the associated table.
        """
        #language=SQL
        install_trigger = """
            CREATE OR REPLACE FUNCTION update_group_count() RETURNS TRIGGER AS $$
            DECLARE
              is_test boolean;
            BEGIN
              -- Contact being added to group
              IF TG_OP = 'INSERT' THEN
                -- Find out if this is a test contact
                SELECT contacts_contact.is_test INTO STRICT is_test FROM contacts_contact WHERE id=NEW.contact_id;

                -- If not
                if not is_test THEN
                  -- Increment our group count
                  UPDATE contacts_contactgroup SET count=count+1 WHERE id=NEW.contactgroup_id;
                END IF;

              -- Contact being removed from a group
              ELSIF TG_OP = 'DELETE' THEN
                -- Find out if this is a test contact
                SELECT contacts_contact.is_test INTO STRICT is_test FROM contacts_contact WHERE id=OLD.contact_id;

                -- If not
                if not is_test THEN
                  -- Decrement our group count
                  UPDATE contacts_contactgroup SET count=count-1 WHERE id=OLD.contactgroup_id;
                END IF;

              -- Table being cleared, reset all counts
              ELSIF TG_OP = 'TRUNCATE' THEN
                UPDATE contacts_contactgroup SET count=0;
              END IF;

              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            -- Install INSERT and DELETE triggers
            DROP TRIGGER IF EXISTS when_contact_groups_changed_then_update_count_trg on contacts_contactgroup_contacts;
            CREATE TRIGGER when_contact_groups_changed_then_update_count_trg
               AFTER INSERT OR DELETE
               ON contacts_contactgroup_contacts
               FOR EACH ROW
               EXECUTE PROCEDURE update_group_count();

            -- Install TRUNCATE trigger
            DROP TRIGGER IF EXISTS when_contact_groups_truncate_then_update_count_trg on contacts_contactgroup_contacts;
            CREATE TRIGGER when_contact_groups_truncate_then_update_count_trg
              AFTER TRUNCATE
              ON contacts_contactgroup_contacts
              EXECUTE PROCEDURE update_group_count();
        """
        cursor = connection.cursor()
        cursor.execute(install_trigger)

    dependencies = [
        ('contacts', '0011_remove_contact_status'),
    ]

    operations = [
        migrations.RunPython(
            calculate_counts,
        ),
        migrations.RunPython(
            install_group_trigger,
        ),
    ]
