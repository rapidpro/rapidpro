# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


# language=SQL
TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION update_label_count() RETURNS TRIGGER AS $$
DECLARE
  is_visible BOOLEAN;
BEGIN
  -- label applied to message
  IF TG_OP = 'INSERT' THEN
    -- is this message visible
    SELECT msgs_msg.visibility = 'V' INTO STRICT is_visible FROM msgs_msg WHERE msgs_msg.id = NEW.msg_id;

    IF is_visible THEN
      UPDATE msgs_label SET visible_count = visible_count + 1 WHERE id = NEW.label_id;
    END IF;

  -- label removed from message
  ELSIF TG_OP = 'DELETE' THEN
    -- is this message visible
    SELECT msgs_msg.visibility = 'V' INTO STRICT is_visible FROM msgs_msg WHERE msgs_msg.id = OLD.msg_id;

    IF is_visible THEN
      UPDATE msgs_label SET visible_count = visible_count - 1 WHERE id = OLD.label_id;
    END IF;

  -- no more labels for any messages
  ELSIF TG_OP = 'TRUNCATE' THEN
    UPDATE msgs_label SET visible_count = 0;

  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- install for INSERT and DELETE on msgs_msg_labels
DROP TRIGGER IF EXISTS when_label_inserted_or_deleted_then_update_count_trg ON msgs_msg_labels;
CREATE TRIGGER when_label_inserted_or_deleted_then_update_count_trg
   AFTER INSERT OR DELETE ON msgs_msg_labels
   FOR EACH ROW EXECUTE PROCEDURE update_label_count();

-- install for TRUNCATE on msgs_msg_labels
DROP TRIGGER IF EXISTS when_labels_truncated_then_update_count_trg ON msgs_msg_labels;
CREATE TRIGGER when_labels_truncated_then_update_count_trg
  AFTER TRUNCATE ON msgs_msg_labels
  EXECUTE PROCEDURE update_label_count();

----------------------------------------------------------------------
-- Trigger procedure to update message user labels on column changes
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_msg_user_label_counts() RETURNS TRIGGER AS $$
BEGIN
  -- is being archived (i.e. no longer included)
  IF OLD.visibility = 'V' AND NEW.visibility = 'A' THEN
    UPDATE msgs_label SET visible_count = visible_count - 1
    FROM msgs_msg_labels
    WHERE msgs_label.label_type = 'L' AND msgs_msg_labels.label_id = msgs_label.id AND msgs_msg_labels.msg_id = NEW.id;
  END IF;

  -- is being restored (i.e. now included)
  IF OLD.visibility = 'A' AND NEW.visibility = 'V' THEN
    UPDATE msgs_label SET visible_count = visible_count + 1
    FROM msgs_msg_labels
    WHERE msgs_label.label_type = 'L' AND msgs_msg_labels.label_id = msgs_label.id AND msgs_msg_labels.msg_id = NEW.id;
  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- install for UPDATE on msgs_msg
DROP TRIGGER IF EXISTS when_msg_updated_then_update_label_counts_trg ON msgs_msg;
CREATE TRIGGER when_msg_updated_then_update_label_counts_trg
  AFTER UPDATE OF visibility ON msgs_msg
  FOR EACH ROW EXECUTE PROCEDURE update_msg_user_label_counts();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0019_unlabel_test_messages'),
    ]

    operations = [
        migrations.RunSQL(TRIGGER_SQL)
    ]
