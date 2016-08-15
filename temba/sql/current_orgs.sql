----------------------------------------------------------------------------------
-- No longer used
----------------------------------------------------------------------------------
DROP FUNCTION IF EXISTS update_topup_used();
DROP FUNCTION IF EXISTS temba_maybe_squash_topupcredits();
DROP TRIGGER IF EXISTS when_msgs_update_then_update_topup_trg on msgs_msg;
DROP TRIGGER IF EXISTS when_msgs_truncate_then_update_topup_trg on msgs_msg;
DROP TRIGGER IF EXISTS temba_when_msgs_truncate_then_update_topupcredits on msgs_msg;

----------------------------------------------------------------------------------
-- Squashes the topup credits for a single topup into a single row
----------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_squash_topupcredits(_topup_id INTEGER)
RETURNS VOID AS $$
BEGIN
  WITH deleted as (DELETE FROM orgs_topupcredits
    WHERE "topup_id" = _topup_id
    RETURNING "used")
    INSERT INTO orgs_topupcredits("topup_id", "used")
    VALUES (_topup_id, GREATEST(0, (SELECT SUM("used") FROM deleted)));
END;
$$ LANGUAGE plpgsql;

---------------------------------------------------------------------------------
-- Increment or decrement the credits used on a topup
---------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION
  temba_insert_topupcredits(_topup_id INT, _count INT)
RETURNS VOID AS $$
BEGIN
  INSERT INTO orgs_topupcredits("topup_id", "used") VALUES(_topup_id, _count);
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------------------
-- Updates our topup credits for the topup being assigned to the Msg
----------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_update_topupcredits() RETURNS TRIGGER AS $$
BEGIN
  -- Msg is being created
  IF TG_OP = 'INSERT' THEN
    -- If we have a topup, increment our # of used credits
    IF NEW.topup_id IS NOT NULL THEN
      PERFORM temba_insert_topupcredits(NEW.topup_id, 1);
    END IF;

  -- Msg is being updated
  ELSIF TG_OP = 'UPDATE' THEN
    -- If the topup has changed
    IF NEW.topup_id IS DISTINCT FROM OLD.topup_id THEN
      -- If our old topup wasn't null then decrement our used credits on it
      IF OLD.topup_id IS NOT NULL THEN
        PERFORM temba_insert_topupcredits(OLD.topup_id, -1);
      END IF;

      -- if our new topup isn't null, then increment our used credits on it
      IF NEW.topup_id IS NOT NULL THEN
        PERFORM temba_insert_topupcredits(NEW.topup_id, 1);
      END IF;
    END IF;

  -- Msg is being deleted
  ELSIF TG_OP = 'DELETE' THEN
    -- Remove a used credit if this Msg had one assigned
    IF OLD.topup_id IS NOT NULL THEN
      PERFORM temba_insert_topupcredits(OLD.topup_id, -1);
    END IF;
  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- install the trigger
DROP TRIGGER IF EXISTS temba_when_msgs_update_then_update_topupcredits on msgs_msg;
CREATE TRIGGER temba_when_msgs_update_then_update_topupcredits
   AFTER INSERT OR DELETE OR UPDATE OF topup_id
   ON msgs_msg
   FOR EACH ROW
   EXECUTE PROCEDURE temba_update_topupcredits();

----------------------------------------------------------------------------------
-- Updates our topup credits for the topup being assigned to a Debit
----------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_update_topupcredits_for_debit() RETURNS TRIGGER AS $$
BEGIN
  -- Debit is being created
  IF TG_OP = 'INSERT' THEN
    -- If we are an allocation and have a topup, increment our # of used credits
    IF NEW.topup_id IS NOT NULL AND NEW.debit_type = 'A' THEN
      PERFORM temba_insert_topupcredits(NEW.topup_id, NEW.amount);
    END IF;

  -- Debit is being updated
  ELSIF TG_OP = 'UPDATE' THEN
    -- If the topup has changed
    IF NEW.topup_id IS DISTINCT FROM OLD.topup_id AND NEW.debit_type = 'A' THEN
      -- If our old topup wasn't null then decrement our used credits on it
      IF OLD.topup_id IS NOT NULL THEN
        PERFORM temba_insert_topupcredits(OLD.topup_id, OLD.amount);
      END IF;

      -- if our new topup isn't null, then increment our used credits on it
      IF NEW.topup_id IS NOT NULL THEN
        PERFORM temba_insert_topupcredits(NEW.topup_id, NEW.amount);
      END IF;
    END IF;

  -- Debit is being deleted
  ELSIF TG_OP = 'DELETE' THEN
    -- Remove a used credit if this Debit had one assigned
    IF OLD.topup_id IS NOT NULL AND NEW.debit_type = 'A' THEN
      PERFORM temba_insert_topupcredits(OLD.topup_id, OLD.amount);
    END IF;
  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- install the trigger
DROP TRIGGER IF EXISTS temba_when_debit_update_then_update_topupcredits_for_debit on orgs_debit;
CREATE TRIGGER temba_when_debit_update_then_update_topupcredits_for_debit
   AFTER INSERT OR DELETE OR UPDATE OF topup_id
   ON orgs_debit
   FOR EACH ROW
   EXECUTE PROCEDURE temba_update_topupcredits_for_debit();

