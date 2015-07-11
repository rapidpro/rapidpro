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
-- Utility function to lookup whether a contact is a simulator contact
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_contact_is_test(_contact_id INT) RETURNS BOOLEAN AS $$
DECLARE
  _is_test BOOLEAN;
BEGIN
  SELECT is_test INTO STRICT _is_test FROM contacts_contact WHERE id = _contact_id;
  RETURN _is_test;
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Increment or decrement a system label
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION
  temba_increment_system_label(_org_id INT, _label_type CHAR(1), _add BOOLEAN)
RETURNS VOID AS $$
DECLARE
  _label_id INT;
BEGIN
  -- lookup the system label id
  SELECT id INTO STRICT _label_id FROM msgs_systemlabel WHERE org_id = _org_id AND label_type = _label_type;

  -- bail if label doesn't exist for some inexplicable reason
  IF _label_id IS NULL THEN
    RAISE EXCEPTION 'System label of type % does not exist for org #%', _label_type, _org_id;
  END IF;

  IF _add THEN
    UPDATE msgs_systemlabel SET "count" = "count" + 1 WHERE id = _label_id;
  ELSE
    UPDATE msgs_systemlabel SET "count" = "count" - 1 WHERE id = _label_id;
  END IF;
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Reset (i.e. zero-ize) system labels of the given type across all orgs
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_reset_system_labels(_label_types CHAR(1)[]) RETURNS VOID AS $$
BEGIN
  UPDATE msgs_systemlabel SET "count" = 0 WHERE label_type = ANY(_label_types);
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
-- Determines the (mutually exclusive) system label for a broadcast record
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_broadcast_determine_system_label(_broadcast msgs_broadcast) RETURNS CHAR(1) AS $$
BEGIN
  IF _broadcast.is_active AND _broadcast.schedule_id IS NOT NULL THEN
    RETURN 'E';
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
  IF TG_OP IN ('INSERT', 'UPDATE') THEN
    -- prevent illegal message states
    IF NEW.direction = 'I' AND NEW.status NOT IN ('P', 'H') THEN
      RAISE EXCEPTION 'Incoming messages can only be PENDING or HANDLED';
    END IF;
    IF NEW.direction = 'O' AND NEW.visibility = 'A' THEN
      RAISE EXCEPTION 'Outgoing messages cannot be archived';
    END IF;
  END IF;

  -- new message inserted
  IF TG_OP = 'INSERT' THEN
    -- don't update anything for a test message
    IF temba_contact_is_test(NEW.contact_id) THEN
      RETURN NULL;
    END IF;

    _new_label_type := temba_msg_determine_system_label(NEW);
    IF _new_label_type IS NOT NULL THEN
      PERFORM temba_increment_system_label(NEW.org_id, _new_label_type, true);
    END IF;

  -- existing message updated
  ELSIF TG_OP = 'UPDATE' THEN
    -- don't update anything for a test message
    IF temba_contact_is_test(NEW.contact_id) THEN
      RETURN NULL;
    END IF;

    _old_label_type := temba_msg_determine_system_label(OLD);
    _new_label_type := temba_msg_determine_system_label(NEW);

    IF _old_label_type IS DISTINCT FROM _new_label_type THEN
      IF _old_label_type IS NOT NULL THEN
        PERFORM temba_increment_system_label(OLD.org_id, _old_label_type, false);
      END IF;
      IF _new_label_type IS NOT NULL THEN
        PERFORM temba_increment_system_label(NEW.org_id, _new_label_type, true);
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
    -- don't update anything for a test message
    IF temba_contact_is_test(OLD.contact_id) THEN
      RETURN NULL;
    END IF;

    _old_label_type := temba_msg_determine_system_label(OLD);

    IF _old_label_type IS NOT NULL THEN
      PERFORM temba_increment_system_label(OLD.org_id, _old_label_type, true);
    END IF;

  -- all messages deleted
  ELSIF TG_OP = 'TRUNCATE' THEN
    PERFORM temba_reset_system_labels('{"I", "W", "A", "O", "S", "X"}');

  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- install for INSERT, UPDATE and DELETE on msgs_msg
DROP TRIGGER IF EXISTS temba_msg_on_change_trg ON msgs_msg;
CREATE TRIGGER temba_msg_on_change_trg
  AFTER INSERT OR UPDATE OR DELETE ON msgs_msg
  FOR EACH ROW EXECUTE PROCEDURE temba_msg_on_change();

-- install for TRUNCATE on msgs_msg
DROP TRIGGER IF EXISTS temba_msg_on_truncate_trg ON msgs_msg;
CREATE TRIGGER temba_msg_on_truncate_trg
  AFTER TRUNCATE ON msgs_msg
  EXECUTE PROCEDURE temba_msg_on_change();


----------------------------------------------------------------------
-- Trigger procedure to update system labels on call changes
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_call_on_change() RETURNS TRIGGER AS $$
BEGIN
  -- new call inserted
  IF TG_OP = 'INSERT' THEN
    -- don't update anything for a test call
    IF temba_contact_is_test(NEW.contact_id) THEN
      RETURN NULL;
    END IF;

    IF NEW.is_active THEN
      PERFORM temba_increment_system_label(NEW.org_id, 'C', true);
    END IF;

  -- existing call updated
  ELSIF TG_OP = 'UPDATE' THEN
    -- don't update anything for a test call
    IF temba_contact_is_test(NEW.contact_id) THEN
      RETURN NULL;
    END IF;

    -- is being de-activated
    IF OLD.is_active AND NOT NEW.is_active THEN
      PERFORM temba_increment_system_label(NEW.org_id, 'C', false);
    -- is being re-activated
    ELSIF NOT OLD.is_active AND NEW.is_active THEN
      PERFORM temba_increment_system_label(NEW.org_id, 'C', true);
    END IF;

  -- existing call deleted
  ELSIF TG_OP = 'DELETE' THEN
    -- don't update anything for a test call
    IF temba_contact_is_test(OLD.contact_id) THEN
      RETURN NULL;
    END IF;

    IF OLD.is_active THEN
      PERFORM temba_increment_system_label(OLD.org_id, 'C', false);
    END IF;

  -- all calls deleted
  ELSIF TG_OP = 'TRUNCATE' THEN
    PERFORM temba_reset_system_labels('{"C"}');

  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- install for INSERT, UPDATE and DELETE on msgs_call
DROP TRIGGER IF EXISTS temba_call_on_change_trg ON msgs_call;
CREATE TRIGGER temba_call_on_change_trg
  AFTER INSERT OR UPDATE OR DELETE ON msgs_call
  FOR EACH ROW EXECUTE PROCEDURE temba_call_on_change();

-- install for TRUNCATE on msgs_call
DROP TRIGGER IF EXISTS temba_call_on_truncate_trg ON msgs_call;
CREATE TRIGGER temba_call_on_truncate_trg
  AFTER TRUNCATE ON msgs_call
  EXECUTE PROCEDURE temba_call_on_change();

----------------------------------------------------------------------
-- Trigger procedure to update system labels on broadcast changes
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_broadcast_on_change() RETURNS TRIGGER AS $$
DECLARE
  _is_test BOOLEAN;
  _new_label_type CHAR(1);
  _old_label_type CHAR(1);
BEGIN
  -- new broadcast inserted
  IF TG_OP = 'INSERT' THEN
    -- don't update anything for a test broadcast
    SELECT c.is_test INTO _is_test FROM contacts_contact c
    INNER JOIN msgs_msg m ON m.contact_id = c.id AND m.broadcast_id = NEW.id;
    IF _is_test = TRUE THEN
      RETURN NULL;
    END IF;

    _new_label_type := temba_broadcast_determine_system_label(NEW);
    IF _new_label_type IS NOT NULL THEN
      PERFORM temba_increment_system_label(NEW.org_id, _new_label_type, true);
    END IF;

  -- existing broadcast updated
  ELSIF TG_OP = 'UPDATE' THEN
    -- don't update anything for a test broadcast
    SELECT c.is_test INTO _is_test FROM contacts_contact c
    INNER JOIN msgs_msg m ON m.contact_id = c.id AND m.broadcast_id = NEW.id;
    IF _is_test = TRUE THEN
      RETURN NULL;
    END IF;

    _old_label_type := temba_broadcast_determine_system_label(OLD);
    _new_label_type := temba_broadcast_determine_system_label(NEW);

    IF _old_label_type IS DISTINCT FROM _new_label_type THEN
      IF _old_label_type IS NOT NULL THEN
        PERFORM temba_increment_system_label(OLD.org_id, _old_label_type, false);
      END IF;
      IF _new_label_type IS NOT NULL THEN
        PERFORM temba_increment_system_label(NEW.org_id, _new_label_type, true);
      END IF;
    END IF;

  -- existing broadcast deleted
  ELSIF TG_OP = 'DELETE' THEN
    -- don't update anything for a test broadcast
    SELECT c.is_test INTO _is_test FROM contacts_contact c
    INNER JOIN msgs_msg m ON m.contact_id = c.id AND m.broadcast_id = OLD.id;
    IF _is_test = TRUE THEN
      RETURN NULL;
    END IF;

    _old_label_type := temba_broadcast_determine_system_label(OLD);

    IF _old_label_type IS NOT NULL THEN
      PERFORM temba_increment_system_label(OLD.org_id, _old_label_type, true);
    END IF;

  -- all broadcast deleted
  ELSIF TG_OP = 'TRUNCATE' THEN
    PERFORM temba_reset_system_labels('{"E"}');

  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- install for INSERT, UPDATE and DELETE on msgs_broadcast
DROP TRIGGER IF EXISTS temba_broadcast_on_change_trg ON msgs_broadcast;
CREATE TRIGGER temba_broadcast_on_change_trg
  AFTER INSERT OR UPDATE OR DELETE ON msgs_broadcast
  FOR EACH ROW EXECUTE PROCEDURE temba_broadcast_on_change();

-- install for TRUNCATE on msgs_broadcast
DROP TRIGGER IF EXISTS temba_broadcast_on_truncate_trg ON msgs_broadcast;
CREATE TRIGGER temba_broadcast_on_truncate_trg
  AFTER TRUNCATE ON msgs_broadcast
  EXECUTE PROCEDURE temba_broadcast_on_change();

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
        ('msgs', '0025_create_system_labels'),
    ]

    operations = [
        migrations.RunSQL(TRIGGER_SQL)
    ]
