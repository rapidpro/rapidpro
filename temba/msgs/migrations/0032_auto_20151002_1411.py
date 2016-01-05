# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations

# language=SQL
TRIGGER_SQL = """
----------------------------------------------------------------------------------
-- Every 1000 inserts or so this will squash the label by gathering the counts
----------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_maybe_squash_systemlabel(_org_id INTEGER, _label_type CHAR(1))
RETURNS VOID AS $$
BEGIN
  IF RANDOM() < .001 THEN
    -- Acquire a lock on the org so we don't deadlock if another thread does this at the same time
    PERFORM "id" from orgs_org where "id" = _org_id FOR UPDATE;

    WITH deleted as (DELETE FROM msgs_systemlabel
      WHERE "org_id" = _org_id AND "label_type" = _label_type
      RETURNING "count")
      INSERT INTO msgs_systemlabel("org_id", "label_type", "count")
      VALUES (_org_id, _label_type, GREATEST(0, (SELECT SUM("count") FROM deleted)));
  END IF;
END;
$$ LANGUAGE plpgsql;
"""

class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0031_msg_contact_urn_optional'),
    ]

    operations = [
        migrations.RunSQL(TRIGGER_SQL)
    ]
