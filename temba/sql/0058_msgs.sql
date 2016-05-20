----------------------------------------------------------------------
-- Deprecated functions
----------------------------------------------------------------------
DROP FUNCTION IF EXISTS temba_call_on_change();

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
-- Utility function to lookup whether a contact is a simulator contact
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_channelevent_is_call(_event channels_channelevent) RETURNS BOOLEAN AS $$
BEGIN
  RETURN _event.event_type IN ('mo_call', 'mo_miss', 'mt_call', 'mt_miss');
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
    IF _msg.VISIBILITY = 'V' THEN
      IF _msg.status = 'P' OR _msg.status = 'Q' THEN
        RETURN 'O';
      ELSIF _msg.status = 'W' OR _msg.status = 'S' OR _msg.status = 'D' THEN
        RETURN 'S';
      ELSIF _msg.status = 'F' THEN
        RETURN 'X';
      END IF;
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
-- Trigger procedure to update system labels on channel event changes
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_channelevent_on_change() RETURNS TRIGGER AS $$
BEGIN
  -- new event inserted
  IF TG_OP = 'INSERT' THEN
    -- don't update anything for a non-call event or test call
    IF NOT temba_channelevent_is_call(NEW) OR temba_contact_is_test(NEW.contact_id) THEN
      RETURN NULL;
    END IF;

    IF NEW.is_active THEN
      PERFORM temba_insert_system_label(NEW.org_id, 'C', 1);
    END IF;

  -- existing call updated
  ELSIF TG_OP = 'UPDATE' THEN
    -- don't update anything for a non-call event or test call
    IF NOT temba_channelevent_is_call(NEW) OR temba_contact_is_test(NEW.contact_id) THEN
      RETURN NULL;
    END IF;

    -- is being de-activated
    IF OLD.is_active AND NOT NEW.is_active THEN
      PERFORM temba_insert_system_label(NEW.org_id, 'C', -1);
    -- is being re-activated
    ELSIF NOT OLD.is_active AND NEW.is_active THEN
      PERFORM temba_insert_system_label(NEW.org_id, 'C', 1);
    END IF;

  -- existing call deleted
  ELSIF TG_OP = 'DELETE' THEN
    -- don't update anything for a test call
    IF NOT temba_channelevent_is_call(OLD) OR temba_contact_is_test(OLD.contact_id) THEN
      RETURN NULL;
    END IF;

    IF OLD.is_active THEN
      PERFORM temba_insert_system_label(OLD.org_id, 'C', -1);
    END IF;

  -- all calls deleted
  ELSIF TG_OP = 'TRUNCATE' THEN
    PERFORM temba_reset_system_labels('{"C"}');

  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- install for INSERT, UPDATE and DELETE on channels_channelevent
DROP TRIGGER IF EXISTS temba_channelevent_on_change_trg ON channels_channelevent;
CREATE TRIGGER temba_channelevent_on_change_trg
  AFTER INSERT OR UPDATE OR DELETE ON channels_channelevent
  FOR EACH ROW EXECUTE PROCEDURE temba_channelevent_on_change();

-- install for TRUNCATE on channels_channelevent
DROP TRIGGER IF EXISTS temba_channelevent_on_truncate_trg ON channels_channelevent;
CREATE TRIGGER temba_channelevent_on_truncate_trg
  AFTER TRUNCATE ON channels_channelevent
  EXECUTE PROCEDURE temba_channelevent_on_change();

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
    IF NEW.recipient_count = 1 THEN
      SELECT c.is_test INTO _is_test FROM contacts_contact c
      INNER JOIN msgs_msg m ON m.contact_id = c.id AND m.broadcast_id = NEW.id;
      IF _is_test = TRUE THEN
        RETURN NULL;
      END IF;
    END IF;

    _new_label_type := temba_broadcast_determine_system_label(NEW);
    IF _new_label_type IS NOT NULL THEN
      PERFORM temba_insert_system_label(NEW.org_id, _new_label_type, 1);
    END IF;

  -- existing broadcast updated
  ELSIF TG_OP = 'UPDATE' THEN
    _old_label_type := temba_broadcast_determine_system_label(OLD);
    _new_label_type := temba_broadcast_determine_system_label(NEW);

    IF _old_label_type IS DISTINCT FROM _new_label_type THEN
      -- if this could be a test broadcast, check it and exit if so
      IF NEW.recipient_count = 1 THEN
        SELECT c.is_test INTO _is_test FROM contacts_contact c
        INNER JOIN msgs_msg m ON m.contact_id = c.id AND m.broadcast_id = NEW.id;
        IF _is_test = TRUE THEN
          RETURN NULL;
        END IF;
      END IF;

      IF _old_label_type IS NOT NULL THEN
        PERFORM temba_insert_system_label(OLD.org_id, _old_label_type, -1);
      END IF;
      IF _new_label_type IS NOT NULL THEN
        PERFORM temba_insert_system_label(NEW.org_id, _new_label_type, 1);
      END IF;
    END IF;

  -- existing broadcast deleted
  ELSIF TG_OP = 'DELETE' THEN
    -- don't update anything for a test broadcast
    IF OLD.recipient_count = 1 THEN
      SELECT c.is_test INTO _is_test FROM contacts_contact c
      INNER JOIN msgs_msg m ON m.contact_id = c.id AND m.broadcast_id = OLD.id;
      IF _is_test = TRUE THEN
        RETURN NULL;
      END IF;
    END IF;

    _old_label_type := temba_broadcast_determine_system_label(OLD);

    IF _old_label_type IS NOT NULL THEN
      PERFORM temba_insert_system_label(OLD.org_id, _old_label_type, 1);
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

---------------------------------------------------------------------------------
-- Increment or decrement a system label
---------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION
  temba_insert_system_label(_org_id INT, _label_type CHAR(1), _count INT)
RETURNS VOID AS $$
BEGIN
  INSERT INTO msgs_systemlabel("org_id", "label_type", "count") VALUES(_org_id, _label_type, _count);
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
      PERFORM temba_insert_system_label(NEW.org_id, _new_label_type, 1);
    END IF;

  -- existing message updated
  ELSIF TG_OP = 'UPDATE' THEN
    _old_label_type := temba_msg_determine_system_label(OLD);
    _new_label_type := temba_msg_determine_system_label(NEW);

    IF _old_label_type IS DISTINCT FROM _new_label_type THEN
      -- don't update anything for a test message
      IF temba_contact_is_test(NEW.contact_id) THEN
        RETURN NULL;
      END IF;

      IF _old_label_type IS NOT NULL THEN
        PERFORM temba_insert_system_label(OLD.org_id, _old_label_type, -1);
      END IF;
      IF _new_label_type IS NOT NULL THEN
        PERFORM temba_insert_system_label(NEW.org_id, _new_label_type, 1);
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
      PERFORM temba_insert_system_label(OLD.org_id, _old_label_type, -1);
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

----------------------------------------------------------------------------------
-- Squash the label by gathering the counts into a single row
----------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_squash_systemlabel(_org_id INTEGER, _label_type CHAR(1))
RETURNS VOID AS $$
BEGIN
  WITH deleted as (DELETE FROM msgs_systemlabel
    WHERE "org_id" = _org_id AND "label_type" = _label_type
    RETURNING "count")
    INSERT INTO msgs_systemlabel("org_id", "label_type", "count")
    VALUES (_org_id, _label_type, GREATEST(0, (SELECT SUM("count") FROM deleted)));
END;
$$ LANGUAGE plpgsql;