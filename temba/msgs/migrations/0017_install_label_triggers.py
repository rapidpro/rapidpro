# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


def populate_label_counts(apps, schema_editor):
    """
    Iterate across all our labels, calculate how many visible, non-test messages are in them
    """
    Label = apps.get_model('msgs', 'Label')
    user_labels = Label.objects.filter(label_type='L')
    for label in user_labels:
        label.visible_count = label.msgs.filter(visibility='V', contact__is_test=False).count()
        label.save(update_fields=('visible_count',))


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0016_label_visible_count'),
    ]

    operations = [
        migrations.RunPython(
            populate_label_counts,
        ),
        migrations.RunSQL(
            # language=SQL
            """
            CREATE OR REPLACE FUNCTION update_label_count() RETURNS TRIGGER AS $$
            DECLARE
              is_included boolean;
            BEGIN
              -- label applied to message
              IF TG_TABLE_NAME = 'msgs_msg_labels' AND TG_OP = 'INSERT' THEN
                -- is this message visible and non-test?
                SELECT (msgs_msg.visibility = 'V' AND NOT contacts_contact.is_test) INTO STRICT is_included
                FROM msgs_msg
                INNER JOIN contacts_contact ON contacts_contact.id = msgs_msg.contact_id
                WHERE msgs_msg.id = NEW.msg_id;

                IF is_included THEN
                  UPDATE msgs_label SET visible_count = visible_count + 1 WHERE id=NEW.label_id;
                END IF;

              -- label removed from message
              ELSIF TG_TABLE_NAME = 'msgs_msg_labels' AND TG_OP = 'DELETE' THEN
                -- is this message visible and non-test?
                SELECT (msgs_msg.visibility = 'V' AND NOT contacts_contact.is_test) INTO STRICT is_included
                FROM msgs_msg
                INNER JOIN contacts_contact ON contacts_contact.id = msgs_msg.contact_id
                WHERE msgs_msg.id = OLD.msg_id;

                IF is_included THEN
                  UPDATE msgs_label SET visible_count = visible_count - 1 WHERE id=OLD.label_id;
                END IF;

              -- no more labels for any messages
              ELSIF TG_TABLE_NAME = 'msgs_msg_labels' AND TG_OP = 'TRUNCATE' THEN
                UPDATE msgs_label SET visible_count = 0;

              -- message visibility may have changed
              ELSIF TG_TABLE_NAME = 'msgs_msg' AND TG_OP = 'UPDATE' THEN
                -- is being archived (i.e. no longer included)
                IF OLD.visibility = 'V' AND NEW.visibility = 'A' THEN
                  UPDATE msgs_label SET visible_count = msgs_label.visible_count - 1
                  FROM msgs_msg_labels
                  WHERE msgs_msg_labels.label_id = msgs_label.id AND msgs_msg_labels.msg_id = NEW.id;
                END IF;
                -- is being restored (i.e. now included)
                IF OLD.visibility = 'A' AND NEW.visibility = 'V' THEN
                  UPDATE msgs_label SET visible_count = msgs_label.visible_count + 1
                  FROM msgs_msg_labels
                  WHERE msgs_msg_labels.label_id = msgs_label.id AND msgs_msg_labels.msg_id = NEW.id;
                END IF;
              END IF;

              RETURN NULL;
            END;
            $$ LANGUAGE plpgsql;

            -- Install INSERT and DELETE triggers for msgs_msg_labels
            DROP TRIGGER IF EXISTS when_label_inserted_or_deleted_then_update_count_trg ON msgs_msg_labels;
            CREATE TRIGGER when_label_inserted_or_deleted_then_update_count_trg
               AFTER INSERT OR DELETE ON msgs_msg_labels
               FOR EACH ROW EXECUTE PROCEDURE update_label_count();

            -- Install TRUNCATE trigger for msgs_msg_labels
            DROP TRIGGER IF EXISTS when_labels_truncated_then_update_count_trg ON msgs_msg_labels;
            CREATE TRIGGER when_labels_truncated_then_update_count_trg
              AFTER TRUNCATE ON msgs_msg_labels
              EXECUTE PROCEDURE update_label_count();

            -- Install UPDATE trigger for msgs_msg
            DROP TRIGGER IF EXISTS when_msg_updated_then_update_label_counts_trg ON msgs_msg;
            CREATE TRIGGER when_msg_updated_then_update_label_counts_trg
              AFTER UPDATE OF visibility ON msgs_msg
              FOR EACH ROW EXECUTE PROCEDURE update_label_count();
        """
        ),
    ]
