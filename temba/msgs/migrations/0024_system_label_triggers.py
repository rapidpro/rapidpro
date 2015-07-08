# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations


# language=SQL
TRIGGER_SQL = """
-- no longer used
DROP TRIGGER IF EXISTS when_msg_updated_then_update_label_counts_trg ON msgs_msg;
DROP TRIGGER IF EXISTS when_label_inserted_or_deleted_then_update_count_trg ON msgs_msg_labels;
DROP TRIGGER IF EXISTS when_labels_truncated_then_update_count_trg ON msgs_msg_labels;
DROP FUNCTION IF EXISTS update_label_count();
DROP FUNCTION IF EXISTS update_msg_user_label_counts();

----------------------------------------------------------------------
-- Toggle a system label on a message
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION
  temba_msg_toggle_system_label(_msg msgs_msg, _label_type CHAR(1), _add BOOLEAN)
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

  -- don't maintain associative table for Flows or Sent
  IF _label_type IN ('W', 'S')  THEN
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
CREATE OR REPLACE FUNCTION temba_msg_determine_system_label(_msg msgs_msg) RETURNS CHAR(1) AS $$
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
    IF _msg.status = 'P' OR _msg.status = 'Q' THEN
      RETURN 'O';
    ELSIF _msg.status = 'W' OR _msg.status = 'S' OR _msg.status = 'D' THEN
      RETURN 'S';
    ELSIF _msg.status = 'F' THEN
      RETURN 'X';
    END IF;
  END IF;

  RETURN NULL; -- might not match any label
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Trigger procedure to update user and system labels on column changes
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_msg_on_change() RETURNS TRIGGER AS $$
DECLARE
  _is_test BOOLEAN;
  _new_label_type CHAR(1);
  _old_label_type CHAR(1);
BEGIN
  IF TG_OP != 'DELETE' THEN
    -- prevent illegal message states
    IF NEW.direction = 'I' AND NEW.status NOT IN ('P', 'H') THEN
      RAISE EXCEPTION 'Incoming messages can only be PENDING or HANDLED';
    END IF;
    IF NEW.direction = 'O' AND NEW.visibility = 'A' THEN
      RAISE EXCEPTION 'Outgoing messages cannot be archived';
    END IF;

    SELECT is_test INTO STRICT _is_test FROM contacts_contact WHERE id = NEW.contact_id;
  ELSE
    SELECT is_test INTO STRICT _is_test FROM contacts_contact WHERE id = OLD.contact_id;
  END IF;

  -- don't update anything for a test message
  IF _is_test THEN
    RETURN NULL;
  END IF;

  -- new message inserted
  IF TG_OP = 'INSERT' THEN
    SELECT temba_msg_determine_system_label(NEW) INTO STRICT _new_label_type;
    IF _new_label_type IS NOT NULL THEN
      PERFORM temba_msg_toggle_system_label(NEW, _new_label_type, true);
    END IF;

  -- existing message updated
  ELSIF TG_OP = 'UPDATE' THEN
    SELECT temba_msg_determine_system_label(OLD) INTO STRICT _old_label_type;
    SELECT temba_msg_determine_system_label(NEW) INTO STRICT _new_label_type;

    IF _old_label_type IS DISTINCT FROM _new_label_type THEN
      IF _old_label_type IS NOT NULL THEN
        PERFORM temba_msg_toggle_system_label(NEW, _old_label_type, false);
      END IF;
      IF _new_label_type IS NOT NULL THEN
        PERFORM temba_msg_toggle_system_label(NEW, _new_label_type, true);
      END IF;
    END IF;

    -- is being archived or deleted (i.e. no longer included for user labels)
    IF OLD.visibility = 'V' AND NEW.visibility != 'V' THEN
      UPDATE msgs_label SET visible_count = visible_count - 1
      FROM msgs_msg_labels
      WHERE msgs_label.label_type = 'L' AND msgs_msg_labels.label_id = msgs_label.id AND msgs_msg_labels.msg_id = NEW.id;
    END IF;

    -- is being restored (i.e. now included for user labels)
    IF OLD.visibility != 'V' AND NEW.visibility = 'V' THEN
      UPDATE msgs_label SET visible_count = visible_count + 1
      FROM msgs_msg_labels
      WHERE msgs_label.label_type = 'L' AND msgs_msg_labels.label_id = msgs_label.id AND msgs_msg_labels.msg_id = NEW.id;
    END IF;

  -- existing message deleted
  ELSIF TG_OP = 'DELETE' THEN
    SELECT temba_msg_determine_system_label(OLD) INTO STRICT _old_label_type;

    IF _old_label_type IS NOT NULL THEN
      PERFORM temba_msg_toggle_system_label(OLD, _old_label_type, true);
    END IF;
  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- install for INSERT, UPDATE and DELETE on msgs_msg
DROP TRIGGER IF EXISTS temba_msg_on_change_trg ON msgs_msg;
CREATE TRIGGER temba_msg_on_change_trg
  AFTER INSERT OR UPDATE OR DELETE ON msgs_msg
  FOR EACH ROW EXECUTE PROCEDURE temba_msg_on_change();

----------------------------------------------------------------------
-- Trigger procedure to maintain user label counts
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_msg_labels_on_change() RETURNS TRIGGER AS $$
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
DROP TRIGGER IF EXISTS temba_msg_labels_on_change_trg ON msgs_msg_labels;
CREATE TRIGGER temba_msg_labels_on_change_trg
   AFTER INSERT OR DELETE ON msgs_msg_labels
   FOR EACH ROW EXECUTE PROCEDURE temba_msg_labels_on_change();

-- install for TRUNCATE on msgs_msg_labels
DROP TRIGGER IF EXISTS temba_msg_labels_on_truncate_trg ON msgs_msg_labels;
CREATE TRIGGER temba_msg_labels_on_truncate_trg
  AFTER TRUNCATE ON msgs_msg_labels
  EXECUTE PROCEDURE temba_msg_labels_on_change();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0023_create_system_labels'),
    ]

    operations = [
        migrations.RunSQL(TRIGGER_SQL)
    ]
