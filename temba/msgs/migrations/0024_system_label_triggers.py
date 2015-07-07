# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations


# language=SQL
TRIGGER_SQL = """
----------------------------------------------------------------------
-- Toggle a system label on a message
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION
  msg_toggle_system_label(_msg msgs_msg, _label_type CHAR(1), _add BOOLEAN)
RETURNS VOID AS $$
DECLARE
  _label_id INT;
BEGIN
  -- lookup the system label id
  SELECT id INTO STRICT _label_id FROM msgs_systemlabel WHERE org_id = _msg.org_id AND label_type = _label_type;

  -- bail if label doesn't exist for some inexplicable reason
  IF _label_id IS NULL THEN
    RAISE EXCEPTION 'System label of type % does not exist for org #%', _label_type, _org_id;
  END IF;

  -- don't maintain associative table for Sent
  IF _label_type = 'S' THEN
    IF _add THEN
      UPDATE msgs_systemlabel SET "count" = "count" + 1 WHERE id = _label_id;
    ELSE
      UPDATE msgs_systemlabel SET "count" = "count" - 1 WHERE id = _label_id;
    END IF;
  ELSE
    IF _add THEN
      BEGIN
        INSERT INTO msgs_systemlabel_msgs (systemlabel_id, msg_id) VALUES (_label_id, _msg.id);
        UPDATE msgs_systemlabel SET "count" = "count" + 1 WHERE id = _label_id;
      EXCEPTION WHEN unique_violation THEN
        -- do nothing as message already had label
      END;
    ELSE
      DELETE FROM msgs_systemlabel_msgs WHERE systemlabel_id = _label_id AND msg_id = _msg.id;
      IF found THEN
        UPDATE msgs_systemlabel SET "count" = "count" - 1 WHERE id = _label_id;
      END IF;
    END IF;
  END IF;
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Determines the (mutually exclusive) system label for a msg record
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION msg_determine_system_label(_msg msgs_msg) RETURNS CHAR(1) AS $$
BEGIN
  IF _msg.direction = 'I' THEN
    IF _msg.visibility = 'V' THEN
      IF _msg.msg_type = 'I' THEN
        RETURN 'I';
      ELSIF _msg.msg_type = 'F' THEN
        RETURN 'W';
      END IF;
    ELSIF _msg.visibility = 'A' THEN
      RETURN 'A';
    END IF;
  ELSE
    IF _msg.status = 'P' OR _msg.status = 'Q' OR _msg.status = 'W' THEN
      RETURN 'O';
    ELSIF _msg.status = 'S' OR _msg.status = 'D' THEN
      RETURN 'S';
    ELSIF _msg.status = 'F' THEN
      RETURN 'X';
    END IF;
  END IF;

  RETURN NULL; -- might not match any label
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Trigger procedure to update message system labels on column changes
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_msg_system_labels() RETURNS TRIGGER AS $$
DECLARE
  _new_label_type CHAR(1);
  _old_label_type CHAR(1);
BEGIN
  -- prevent illegal message states
  IF NEW IS NOT NULL THEN
    IF NEW.direction = 'I' AND NEW.status NOT IN ('P', 'H') THEN
      RAISE EXCEPTION 'Incoming messages can only be PENDING or HANDLED';
    END IF;
    IF NEW.direction = 'O' AND (NEW.visibility = 'A' OR NEW.visibility = 'D') THEN
      RAISE EXCEPTION 'Cannot archive or delete outgoing messages';
    END IF;
  END IF;

  -- new message inserted
  IF TG_OP = 'INSERT' THEN
    SELECT msg_determine_system_label(NEW) INTO STRICT _new_label_type;
    IF _new_label_type IS NOT NULL THEN
      PERFORM msg_toggle_system_label(NEW, _new_label_type, true);
    END IF;

  -- existing message updated
  ELSIF TG_OP = 'UPDATE' THEN
    SELECT msg_determine_system_label(OLD) INTO STRICT _old_label_type;
    SELECT msg_determine_system_label(NEW) INTO STRICT _new_label_type;

    IF _old_label_type IS DISTINCT FROM _new_label_type THEN
      IF _old_label_type IS NOT NULL THEN
        PERFORM msg_toggle_system_label(NEW, _old_label_type, false);
      END IF;
      IF _new_label_type IS NOT NULL THEN
        PERFORM msg_toggle_system_label(NEW, _new_label_type, true);
      END IF;
    END IF;

  -- existing message deleted
  ELSIF TG_OP = 'DELETE' THEN
    SELECT msg_determine_system_label(OLD) INTO STRICT _old_label_type;

    IF _old_label_type IS NOT NULL THEN
      PERFORM msg_toggle_system_label(OLD, _old_label_type, true);
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
