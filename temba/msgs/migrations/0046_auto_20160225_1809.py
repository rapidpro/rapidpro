# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models

INDEX_SQL = """
DO $$
BEGIN

IF NOT EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE c.relname = 'msg_direction_modified_inbound' AND n.nspname = 'public') THEN
    CREATE INDEX msg_direction_modified_inbound ON msgs_msg (org_id, direction, modified_on DESC)
      WHERE direction = 'I';
END IF;

END$$;"""

class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0045_populate_modified_on'),
    ]

    operations = [
        migrations.RunSQL(INDEX_SQL)
    ]
