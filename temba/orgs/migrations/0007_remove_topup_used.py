# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


# language=SQL
TRIGGER_SQL = """
---------------------------------------------------------------------------------
-- Increment or decrement the credits used on a topup
---------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION
  temba_insert_topupcredits(_topup_id INT, _count INT)
RETURNS VOID AS $$
BEGIN
  INSERT INTO orgs_topupcredits("topup_id", "used") VALUES(_topup_id, _count);
  PERFORM temba_maybe_squash_topupcredits(_topup_id);
END;
$$ LANGUAGE plpgsql;

----------------------------------------------------------------------------------
-- Every 100 inserts or so this will squash the credits by gathering them
----------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_maybe_squash_topupcredits(_topup_id INTEGER)
RETURNS VOID AS $$
BEGIN
  IF RANDOM() < .01 THEN
    WITH deleted as (DELETE FROM orgs_topupcredits
      WHERE "topup_id" = _topup_id
      RETURNING "used")
      INSERT INTO orgs_topupcredits("topup_id", "used")
      VALUES (_topup_id, GREATEST(0, (SELECT SUM("used") FROM deleted)));
  END IF;
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

  -- Msgs table is being truncated
  ELSIF TG_OP = 'TRUNCATE' THEN
    -- Clear all used credits
    TRUNCATE orgs_topupcredits;

  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS when_msgs_update_then_update_topup_trg on msgs_msg;
DROP TRIGGER IF EXISTS temba_when_msgs_update_then_update_topupcredits on msgs_msg;
CREATE TRIGGER temba_when_msgs_update_then_update_topupcredits
   AFTER INSERT OR DELETE OR UPDATE OF topup_id
   ON msgs_msg
   FOR EACH ROW
   EXECUTE PROCEDURE temba_update_topupcredits();

DROP TRIGGER IF EXISTS when_msgs_truncate_then_update_topup_trg on msgs_msg;
DROP TRIGGER IF EXISTS temba_when_msgs_truncate_then_update_topupcredits on msgs_msg;
CREATE TRIGGER temba_when_msgs_truncate_then_update_topupcredits
  AFTER TRUNCATE
  ON msgs_msg
  EXECUTE PROCEDURE temba_update_topupcredits();

DROP FUNCTION IF EXISTS update_topup_used();
"""

class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0006_topupcredits'),
    ]

    def populate_topupcredits(apps, schema_editor):
        """
        Iterate across all our topups, populate our topup credits table with our current used value
        """
        TopUp = apps.get_model('orgs', 'TopUp')
        TopUpCredits = apps.get_model('orgs', 'TopUpCredits')

        for topup in TopUp.objects.all():
            TopUpCredits.objects.create(topup=topup, used=topup.used)

    operations = [
        migrations.RunPython(populate_topupcredits),
        migrations.RemoveField(
            model_name='topup',
            name='used',
        ),
        migrations.RunSQL(TRIGGER_SQL),
    ]
