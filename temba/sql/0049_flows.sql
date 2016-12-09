----------------------------------------------------------------------
-- Squashes the flowrun counts for a particular flow and exit type
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION
  temba_squash_flowruncount(_flow_id INT, _exit_type CHAR(1))
RETURNS VOID AS $$
BEGIN
  IF _exit_type IS NULL THEN
    WITH removed as (DELETE FROM flows_flowruncount
      WHERE "flow_id" = _flow_id AND "exit_type" IS NULL RETURNING "count")
      INSERT INTO flows_flowruncount("flow_id", "exit_type", "count")
      VALUES (_flow_id, _exit_type, GREATEST(0, (SELECT SUM("count") FROM removed)));
  ELSE
    WITH removed as (DELETE FROM flows_flowruncount
      WHERE "flow_id" = _flow_id AND "exit_type" = _exit_type RETURNING "count")
      INSERT INTO flows_flowruncount("flow_id", "exit_type", "count")
      VALUES (_flow_id, _exit_type, GREATEST(0, (SELECT SUM("count") FROM removed)));
  END IF;
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Inserts a new flowrun_count
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION
  temba_insert_flowruncount(_flow_id INT, _exit_type CHAR(1), _count INT)
RETURNS VOID AS $$
BEGIN
  INSERT INTO flows_flowruncount("flow_id", "exit_type", "count")
  VALUES(_flow_id, _exit_type, _count);
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Increments or decrements our counts for each exit type
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_update_flowruncount() RETURNS TRIGGER AS $$
BEGIN
  -- Table being cleared, reset all counts
  IF TG_OP = 'TRUNCATE' THEN
    TRUNCATE flows_flowruncounts;
    RETURN NULL;
  END IF;

  -- FlowRun being added
  IF TG_OP = 'INSERT' THEN
     -- Is this a test contact, ignore
     IF temba_contact_is_test(NEW.contact_id) THEN
       RETURN NULL;
     END IF;

    -- Increment appropriate type
    PERFORM temba_insert_flowruncount(NEW.flow_id, NEW.exit_type, 1);

  -- FlowRun being removed
  ELSIF TG_OP = 'DELETE' THEN
     -- Is this a test contact, ignore
     IF temba_contact_is_test(OLD.contact_id) THEN
       RETURN NULL;
     END IF;

    PERFORM temba_insert_flowruncount(OLD.flow_id, OLD.exit_type, -1);

  -- Updating exit type
  ELSIF TG_OP = 'UPDATE' THEN
     -- Is this a test contact, ignore
     IF temba_contact_is_test(NEW.contact_id) THEN
       RETURN NULL;
     END IF;

    PERFORM temba_insert_flowruncount(OLD.flow_id, OLD.exit_type, -1);
    PERFORM temba_insert_flowruncount(NEW.flow_id, NEW.exit_type, 1);
  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Install INSERT, UPDATE and DELETE triggers
DROP TRIGGER IF EXISTS temba_flowrun_update_flowruncount on flows_flowrun;
CREATE TRIGGER temba_flowrun_update_flowruncount
   AFTER INSERT OR DELETE OR UPDATE OF exit_type
   ON flows_flowrun
   FOR EACH ROW
   EXECUTE PROCEDURE temba_update_flowruncount();

-- Install TRUNCATE trigger
DROP TRIGGER IF EXISTS temba_flowrun_truncate_flowruncount on flows_flowrun;
CREATE TRIGGER temba_flowrun_truncate_flowruncount
  AFTER TRUNCATE
  ON flows_flowrun
  EXECUTE PROCEDURE temba_update_flowruncount();
