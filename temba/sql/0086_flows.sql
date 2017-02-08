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
      INSERT INTO flows_flowruncount("flow_id", "exit_type", "count", "is_squashed")
      VALUES (_flow_id, _exit_type, GREATEST(0, (SELECT SUM("count") FROM removed)), TRUE);
  ELSE
    WITH removed as (DELETE FROM flows_flowruncount
      WHERE "flow_id" = _flow_id AND "exit_type" = _exit_type RETURNING "count")
      INSERT INTO flows_flowruncount("flow_id", "exit_type", "count", "is_squashed")
      VALUES (_flow_id, _exit_type, GREATEST(0, (SELECT SUM("count") FROM removed)), TRUE);
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
  INSERT INTO flows_flowruncount("flow_id", "exit_type", "count", "is_squashed")
  VALUES(_flow_id, _exit_type, _count, FALSE);
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

----------------------------------------------------------------------
----------------------------------------------------------------------
-- Triggers for managing FlowPathCount squashing
----------------------------------------------------------------------
----------------------------------------------------------------------

----------------------------------------------------------------------
-- Utility function to lookup whether a contact is a simulator contact
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_flows_contact_is_test(_contact_id INT) RETURNS BOOLEAN AS $$
DECLARE
  _is_test BOOLEAN;
BEGIN
  SELECT is_test INTO STRICT _is_test FROM contacts_contact WHERE id = _contact_id;
  RETURN _is_test;
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Inserts a new flowpathcount
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_insert_flowpathcount(_flow_id INTEGER, _from_uuid UUID, _to_uuid UUID, _period TIMESTAMP WITH TIME ZONE, _count INTEGER) RETURNS VOID AS $$
  BEGIN
    INSERT INTO flows_flowpathcount("flow_id", "from_uuid", "to_uuid", "period", "count", "is_squashed")
      VALUES(_flow_id, _from_uuid, _to_uuid, date_trunc('hour', _period), _count, FALSE);
  END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Squashes all the existing flowpathcounts into a single row
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_squash_flowpathcount(_flow_id INTEGER, _from_uuid UUID, _to_uuid UUID, _period TIMESTAMP WITH TIME ZONE) RETURNS VOID AS $$
  BEGIN
    IF _to_uuid IS NULL THEN
      WITH removed as (DELETE FROM flows_flowpathcount
        WHERE "flow_id" = _flow_id AND "from_uuid" = _from_uuid
              AND "to_uuid" IS NULL AND "period" = date_trunc('hour', _period)
        RETURNING "count")
        INSERT INTO flows_flowpathcount("flow_id", "from_uuid", "to_uuid", "period", "count", "is_squashed")
        VALUES (_flow_id, _from_uuid, NULL, date_trunc('hour', _period), GREATEST(0, (SELECT SUM("count") FROM removed)), TRUE);
    ELSE
      WITH removed as (DELETE FROM flows_flowpathcount
        WHERE "flow_id" = _flow_id AND "from_uuid" = _from_uuid
              AND "to_uuid" = _to_uuid AND "period" = date_trunc('hour', _period)
        RETURNING "count")
        INSERT INTO flows_flowpathcount("flow_id", "from_uuid", "to_uuid", "period", "count", "is_squashed")
        VALUES (_flow_id, _from_uuid, _to_uuid, date_trunc('hour', _period), GREATEST(0, (SELECT SUM("count") FROM removed)), TRUE);
    END IF;
  END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Utility function to fetch the flow id from a run
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_flow_for_run(_run_id INT) RETURNS INTEGER AS $$
DECLARE
  _flow_id INTEGER;
BEGIN
  SELECT flow_id INTO STRICT _flow_id FROM flows_flowrun WHERE id = _run_id;
  RETURN _flow_id;
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Utility function to return the appropriate from uuid
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_step_from_uuid(_row flows_flowstep) RETURNS UUID AS $$
BEGIN
  IF _row.rule_uuid IS NOT NULL THEN
    RETURN uuid(_row.rule_uuid);
  ELSIF _row.step_uuid IS NOT NULL THEN
    RETURN uuid(_row.step_uuid);
  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------
-- Keeps track of our flowpathcounts as steps are updated
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_update_flowpathcount() RETURNS TRIGGER AS $$
DECLARE is_test boolean;
DECLARE from_uuid UUID;
DECLARE to_uuid UUID;
DECLARE flow_id int;
BEGIN

  IF TG_OP = 'TRUNCATE' THEN
    -- Table being cleared, reset all counts
    DELETE FROM flows_flowpathcount;

  -- FlowStep being added, increment if next is set
  ELSIF TG_OP = 'DELETE' THEN

    -- see where we came from and were going to
    from_uuid = temba_step_from_uuid(OLD);
    IF from_uuid IS NULL THEN
      RETURN NULL;
    END IF;

    to_uuid = NULL;
    IF OLD.next_uuid IS NOT NULL THEN
      to_uuid = uuid(OLD.next_uuid);
    END IF;

    IF OLD.left_on IS NOT NULL THEN
      PERFORM temba_insert_flowpathcount(temba_flow_for_run(OLD.run_id), from_uuid, to_uuid, OLD.left_on, -1);
    END IF;

  ELSIF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN

    -- we only operate if left_on is set and we are a real contact
    IF NEW.left_on IS NULL THEN
      RETURN NULL;
    ELSE
      is_test = temba_flows_contact_is_test(NEW.contact_id);
      IF is_test THEN
        RETURN NULL;
      END IF;
    END IF;

    -- see where we are coming from and where we are going
    from_uuid = temba_step_from_uuid(NEW);
    IF from_uuid IS NULL THEN
      RETURN NULL;
    END IF;

    to_uuid = NULL;
    IF NEW.next_uuid IS NOT NULL THEN
      to_uuid = uuid(NEW.next_uuid);
    END IF;

    -- our flow id we are working with
    flow_id = temba_flow_for_run(NEW.run_id);

    PERFORM temba_insert_flowpathcount(flow_id, from_uuid, to_uuid, NEW.left_on, 1);

    -- Remove our old count
    IF TG_OP = 'UPDATE' THEN

      from_uuid = temba_step_from_uuid(OLD);
      to_uuid = NULL;
      IF OLD.next_uuid IS NOT NULL THEN
        to_uuid = uuid(OLD.next_uuid);
      END IF;

      IF OLD.left_on IS NOT NULL THEN
        PERFORM temba_insert_flowpathcount(flow_id, from_uuid, to_uuid, OLD.left_on, -1);
      END IF;
    END IF;
  END IF;
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Install INSERT, UPDATE and DELETE triggers
DROP TRIGGER IF EXISTS temba_flowstep_update_flowpathcount on flows_flowstep;
CREATE TRIGGER temba_flowstep_update_flowpathcount
   AFTER INSERT OR DELETE OR UPDATE OF left_on
   ON flows_flowstep
   FOR EACH ROW
   EXECUTE PROCEDURE temba_update_flowpathcount();

-- Install TRUNCATE trigger
DROP TRIGGER IF EXISTS temba_flowstep_truncate_flowpathcount on flows_flowstep;
CREATE TRIGGER temba_flowstep_truncate_flowpathcount
  AFTER TRUNCATE
  ON flows_flowstep
  EXECUTE PROCEDURE temba_update_flowpathcount();
