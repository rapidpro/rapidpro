# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models

INDEX_SQL = """
 DO $$
 BEGIN

 IF NOT EXISTS (
     SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.relname = 'msgs_msg_responded_to_not_null' AND n.nspname = 'public') THEN
     CREATE INDEX msgs_msg_responded_to_not_null ON msgs_msg (response_to_id) WHERE response_to_id IS NOT NULL;
 END IF;

 END$$;"""

class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0047_auto_20160307_1919'),
    ]

    operations = [
        migrations.RunSQL(INDEX_SQL),
    ]
