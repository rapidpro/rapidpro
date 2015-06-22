# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0017_remove_contact_fields'),
    ]

    operations = [
        migrations.RunSQL(
            # language=SQL
            """
            ----------------------------------------------------------------------
            -- Toggle a contact's membership of a system group in their org
            ----------------------------------------------------------------------
            CREATE OR REPLACE FUNCTION
              contact_toggle_system_group(_contact_id INT, _org_id INT, _group_type CHAR(1), _add BOOLEAN)
            RETURNS VOID AS $$
            DECLARE
              _group_id INT;
            BEGIN
              -- lookup the group id
              SELECT id INTO STRICT _group_id FROM contacts_contactgroup
              WHERE org_id = _org_id AND group_type = _group_type;

              -- don't do anything if group doesn't exist for some inexplicable reason
              IF _group_id IS NULL THEN
                RETURN;
              END IF;

              IF _add THEN
                BEGIN
                  INSERT INTO contacts_contactgroup_contacts (contactgroup_id, contact_id) VALUES (_group_id, _contact_id);
                EXCEPTION WHEN unique_violation THEN
                  -- do nothing
                END;
              ELSE
                DELETE FROM contacts_contactgroup_contacts WHERE contactgroup_id = _group_id AND contact_id = _contact_id;
              END IF;
            END;
            $$ LANGUAGE plpgsql;

            ----------------------------------------------------------------------
            -- Convenience method to call contact_toggle_system_group with a row
            ----------------------------------------------------------------------
            CREATE OR REPLACE FUNCTION
              contact_toggle_system_group(_contact contacts_contact, _group_type CHAR(1), _add BOOLEAN)
            RETURNS VOID AS $$
            DECLARE
              _group_id INT;
            BEGIN
              PERFORM contact_toggle_system_group(_contact.id, _contact.org_id, _group_type, _add);
            END;
            $$ LANGUAGE plpgsql;


            CREATE OR REPLACE FUNCTION update_contact_system_groups() RETURNS TRIGGER AS $$
            BEGIN
              -- new contact added
              IF TG_OP = 'INSERT' AND NOT NEW.is_test THEN
                IF NEW.is_blocked THEN
                  PERFORM contact_toggle_system_group(NEW, 'B', true);
                ELSE
                  PERFORM contact_toggle_system_group(NEW, 'A', true);
                  IF NEW.is_failed THEN
                      PERFORM contact_toggle_system_group(NEW, 'F', true);
                  END IF;
                END IF;
              END IF;
              -- existing contact updated
              IF TG_OP = 'UPDATE' AND NOT NEW.is_test THEN
                -- is being blocked
                IF NOT OLD.is_blocked AND NEW.is_blocked THEN
                  PERFORM contact_toggle_system_group(NEW, 'A', false);
                  PERFORM contact_toggle_system_group(NEW, 'B', true);
                  PERFORM contact_toggle_system_group(NEW, 'F', false);
                END IF;
                -- is being unblocked
                IF OLD.is_blocked AND NOT NEW.is_blocked THEN
                  PERFORM contact_toggle_system_group(NEW, 'A', true);
                  PERFORM contact_toggle_system_group(NEW, 'B', false);
                  IF NEW.is_failed THEN
                    PERFORM contact_toggle_system_group(NEW, 'F', true);
                  END IF;
                END IF;
                -- is being failed
                IF NOT OLD.is_failed AND NEW.is_failed THEN
                  PERFORM contact_toggle_system_group(NEW, 'F', true);
                END IF;
                -- is being unfailed
                IF OLD.is_failed AND NOT NEW.is_failed THEN
                  PERFORM contact_toggle_system_group(NEW, 'F', false);
                END IF;
                -- is being released
                IF OLD.is_active AND NOT NEW.is_active THEN
                  PERFORM contact_toggle_system_group(NEW, 'A', false);
                  PERFORM contact_toggle_system_group(NEW, 'B', false);
                  PERFORM contact_toggle_system_group(NEW, 'F', false);
                END IF;
                -- is being unreleased
                IF NOT OLD.is_active AND NEW.is_active THEN
                  PERFORM contact_toggle_system_group(NEW, 'A', true);
                  IF NEW.is_failed THEN
                    PERFORM contact_toggle_system_group(NEW, 'F', true);
                  END IF;
                END IF;
              END IF;

              RETURN NULL;
            END;
            $$ LANGUAGE plpgsql;

            -- install for INSERT and DELETE on contacts_contact
            DROP TRIGGER IF EXISTS when_contacts_changed_then_update_groups_trg ON contacts_contact;
            CREATE TRIGGER when_contacts_changed_then_update_groups_trg
               AFTER INSERT OR UPDATE ON contacts_contact
               FOR EACH ROW EXECUTE PROCEDURE update_contact_system_groups();
        """)
    ]
