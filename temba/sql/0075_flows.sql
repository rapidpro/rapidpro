----------------------------------------------------------------------
-- Squashes all the existing flowpathcounts into a single row
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_squash_flowpathcount(_flow_id INTEGER, _from_uuid UUID, _to_uuid UUID, _period TIMESTAMP WITH TIME ZONE) RETURNS VOID AS $$
  BEGIN
    WITH removed as (DELETE FROM flows_flowpathcount
      WHERE "flow_id" = _flow_id AND "from_uuid" = _from_uuid
            AND "to_uuid" = _to_uuid AND "period" = date_trunc('hour', _period)
      RETURNING "count")
      INSERT INTO flows_flowpathcount("flow_id", "from_uuid", "to_uuid", "period", "count")
      VALUES (_flow_id, _from_uuid, _to_uuid, date_trunc('hour', _period), GREATEST(0, (SELECT SUM("count") FROM removed)));
  END;
$$ LANGUAGE plpgsql;