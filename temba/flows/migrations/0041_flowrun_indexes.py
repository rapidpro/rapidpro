# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


INDEX_SQL = """
DO $$
BEGIN

IF NOT EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relname = 'flows_flowrun_flow_id_modified_on' AND n.nspname = 'public') THEN
    CREATE INDEX flows_flowrun_flow_id_modified_on ON flows_flowrun (flow_id, modified_on DESC);
END IF;

IF NOT EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relname = 'flows_flowrun_null_expired_on' AND n.nspname = 'public') THEN
    CREATE INDEX "flows_flowrun_null_expired_on" ON flows_flowrun (expired_on) WHERE expired_on IS NULL;
END IF;

IF NOT EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relname = 'flows_flowrun_org_id_modified_on' AND n.nspname = 'public') THEN
    CREATE INDEX "flows_flowrun_org_id_modified_on" ON flows_flowrun (org_id, modified_on DESC);
END IF;

END$$;"""


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0040_auto_20151103_1014'),
    ]

    operations = [
        migrations.RunSQL(INDEX_SQL)
    ]
