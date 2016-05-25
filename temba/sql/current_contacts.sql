----------------------------------------------------------------------
-- Trigger procedure to update group count
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_group_count() RETURNS TRIGGER AS $$
DECLARE
  is_test BOOLEAN;
BEGIN
  -- contact being added to group
  IF TG_OP = 'INSERT' THEN
    -- is this a test contact
    SELECT contacts_contact.is_test INTO STRICT is_test FROM contacts_contact WHERE id = NEW.contact_id;

    IF NOT is_test THEN
      INSERT INTO contacts_contactgroupcount("group_id", "count") VALUES(NEW.contactgroup_id, 1);
    END IF;

  -- contact being removed from a group
  ELSIF TG_OP = 'DELETE' THEN
    -- is this a test contact
    SELECT contacts_contact.is_test INTO STRICT is_test FROM contacts_contact WHERE id = OLD.contact_id;

    IF NOT is_test THEN
      INSERT INTO contacts_contactgroupcount("group_id", "count") VALUES(OLD.contactgroup_id, -1);
    END IF;

  -- table being cleared, clear our counts
  ELSIF TG_OP = 'TRUNCATE' THEN
    TRUNCATE contacts_contactgroupcount;
  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- install for INSERT and DELETE on contacts_contactgroup_contacts
DROP TRIGGER IF EXISTS when_contact_groups_changed_then_update_count_trg on contacts_contactgroup_contacts;
CREATE TRIGGER when_contact_groups_changed_then_update_count_trg
   AFTER INSERT OR DELETE ON contacts_contactgroup_contacts
   FOR EACH ROW EXECUTE PROCEDURE update_group_count();

-- install for TRUNCATE on contacts_contactgroup_contacts
DROP TRIGGER IF EXISTS when_contact_groups_truncate_then_update_count_trg on contacts_contactgroup_contacts;
CREATE TRIGGER when_contact_groups_truncate_then_update_count_trg
  AFTER TRUNCATE ON contacts_contactgroup_contacts
  EXECUTE PROCEDURE update_group_count();

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

----------------------------------------------------------------------
-- Trigger procedure to update contact system groups on column changes
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_contact_system_groups() RETURNS TRIGGER AS $$
BEGIN
  -- new contact added
  IF TG_OP = 'INSERT' AND NEW.is_active AND NOT NEW.is_test THEN
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
    -- do nothing for inactive contacts
    IF NOT OLD.is_active AND NOT NEW.is_active THEN
      RETURN NULL;
    END IF;

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
      IF NOT NEW.is_blocked THEN
        PERFORM contact_toggle_system_group(NEW, 'A', true);
      ELSE
        PERFORM contact_toggle_system_group(NEW, 'B', true);
      END IF;
      IF NEW.is_failed THEN
        PERFORM contact_toggle_system_group(NEW, 'F', true);
      END IF;
    END IF;

  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- install for INSERT and UPDATE on contacts_contact
DROP TRIGGER IF EXISTS when_contacts_changed_then_update_groups_trg ON contacts_contact;
CREATE TRIGGER when_contacts_changed_then_update_groups_trg
   AFTER INSERT OR UPDATE ON contacts_contact
   FOR EACH ROW EXECUTE PROCEDURE update_contact_system_groups();

----------------------------------------------------------------------
-- Trigger procedure to prevent illegal state changes to contacts
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION contact_check_update() RETURNS TRIGGER AS $$
BEGIN
  IF OLD.is_test != NEW.is_test THEN
    RAISE EXCEPTION 'Contact.is_test cannot be changed';
  END IF;

  IF NEW.is_test AND (NEW.is_blocked OR NEW.is_failed) THEN
    RAISE EXCEPTION 'Test contacts cannot be blocked or failed';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- install for UPDATE on contacts_contact
DROP TRIGGER IF EXISTS contact_check_update_trg ON contacts_contact;
CREATE TRIGGER contact_check_update_trg
   BEFORE UPDATE OF is_test, is_blocked, is_failed ON contacts_contact
   FOR EACH ROW EXECUTE PROCEDURE contact_check_update();

----------------------------------------------------------------------------------
-- Squash the group counts by gathering the counts into a single row
----------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_squash_contactgroupcounts(_group_id INTEGER)
RETURNS VOID AS $$
BEGIN
  WITH deleted as (DELETE FROM contacts_contactgroupcount
    WHERE "group_id" = _group_id RETURNING "count")
    INSERT INTO contacts_contactgroupcount("group_id", "count")
    VALUES (_group_id, GREATEST(0, (SELECT SUM("count") FROM deleted)));
END;
$$ LANGUAGE plpgsql;
