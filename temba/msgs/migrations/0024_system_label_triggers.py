# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations


# language=SQL
TRIGGER_SQL = """
----------------------------------------------------------------------
-- Toggle a system label on a message
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION
  msg_toggle_system_label(_msg_id INT, _org_id INT, _label_type CHAR(1), _add BOOLEAN)
RETURNS VOID AS $$
DECLARE
  _label_id INT;
BEGIN
  -- lookup the label id
  SELECT id INTO STRICT _label_id FROM msgs_label
  WHERE org_id = _org_id AND label_type = _label_type;

  -- bail if label doesn't exist for some inexplicable reason
  IF _label_id IS NULL THEN
    RAISE EXCEPTION 'System label of type % does not exist for org #%', _label_type, _org_id;
  END IF;

  IF _add THEN
    BEGIN
      INSERT INTO msgs_systemlabel_msgs (systemlabel_id, msg_id) VALUES (_label_id, _msg_id);
      UPDATE msgs_systemlabel SET "count" = "count" + 1 WHERE id = _label_id;
    EXCEPTION WHEN unique_violation THEN
      -- do nothing as message already had label
    END;
  ELSE
    DELETE FROM msgs_msg_labels WHERE label_id = _label_id AND msg_id = _msg_id;
    IF found THEN
      UPDATE msgs_systemlabel SET "count" = "count" - 1 WHERE id = _label_id;
    END IF;
  END IF;
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Convenience method to call msg_toggle_system_label with a row
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION
  msg_toggle_system_label(_msg msgs_msg, _label_type CHAR(1), _add BOOLEAN)
RETURNS VOID AS $$
BEGIN
  PERFORM msg_toggle_system_label(_msg.id, _msg.org_id, _label_type, _add);
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Trigger procedure to update message system labels on column changes
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_msg_system_labels() RETURNS TRIGGER AS $$
BEGIN
  IF NEW.direction = 'O' AND NEW.visibility = 'A' THEN
    RAISE EXCEPTION 'Cannot archive outgoing messages';
  END IF;

  -- new message added
  IF TG_OP = 'INSERT' THEN
    IF NEW.direction = 'I' THEN
      IF NEW.visibility = 'V' THEN
        IF NEW.msg_type = 'I' THEN
          PERFORM msg_toggle_system_label(NEW, 'I', true);
        ELSIF NEW.msg_type = 'F' THEN
          PERFORM msg_toggle_system_label(NEW, 'W', true);
        END IF;
      ELSIF NEW.visibility = 'A' THEN
        PERFORM msg_toggle_system_label(NEW, 'A', true);
      END IF;
    ELSIF NEW.direction = 'O' THEN
      IF NEW.status = 'Q' THEN
        PERFORM msg_toggle_system_label(NEW, 'O', true);
      ELSIF NEW.status = 'S' THEN
        PERFORM increment_system_label(NEW.org_id, 'S', 1);
      ELSIF NEW.status = 'F' THEN
        PERFORM msg_toggle_system_label(NEW, 'X', true);
      END IF;
    END IF;

  -- existing message updated
  ELSIF TG_OP = 'UPDATE' THEN
    -- is being classified
    IF OLD.msg_type IS NULL AND NEW.msg_type IS NOT NULL THEN
      IF NEW.msg_type = 'I' THEN -- as INBOX
        PERFORM msg_toggle_system_label(NEW, 'I', true);
      ELSIF NEW.msg_type = 'F' THEN -- as FLOW
        PERFORM msg_toggle_system_label(NEW, 'W', true);
      END IF;

    -- is being archived
    IF OLD.visibility = 'V' AND NEW.visibility = 'A' THEN

    -- is being restored
    ELSIF OLD.visibility = 'A' AND NEW.visibility = 'V' THEN

    END IF;

  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- install for INSERT and UPDATE on msgs_msg
DROP TRIGGER IF EXISTS when_msgs_changed_then_update_system_labels_trg ON msgs_msg;
CREATE TRIGGER when_msgs_changed_then_update_system_labels_trg
  AFTER INSERT OR UPDATE ON msgs_msg
  FOR EACH ROW EXECUTE PROCEDURE update_msg_system_labels();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0023_create_system_labels'),
    ]

    operations = [
        migrations.RunSQL(TRIGGER_SQL)
    ]
