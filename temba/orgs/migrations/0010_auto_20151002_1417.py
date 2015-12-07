# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations

# language=SQL
TRIGGER_SQL = """
----------------------------------------------------------------------------------
-- Every 1,000 inserts or so this will squash the credits by gathering them
----------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_maybe_squash_topupcredits(_topup_id INTEGER)
RETURNS VOID AS $$
BEGIN
  IF RANDOM() < .001 THEN
    -- Acquire a lock on this topup so there is only one squash at a time
    PERFORM "id" FROM orgs_topup WHERE "id" = _topup_id FOR UPDATE;

    WITH deleted as (DELETE FROM orgs_topupcredits
      WHERE "topup_id" = _topup_id
      RETURNING "used")
      INSERT INTO orgs_topupcredits("topup_id", "used")
      VALUES (_topup_id, GREATEST(0, (SELECT SUM("used") FROM deleted)));
  END IF;
END;
$$ LANGUAGE plpgsql;
"""

class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0009_org_surveyors'),
    ]

    operations = [
    ]
