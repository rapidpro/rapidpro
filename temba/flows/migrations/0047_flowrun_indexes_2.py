# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations


INDEX_SQL = """
DO $$
BEGIN

IF NOT EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relname = 'flows_flowrun_org_id_modified_on_responded' AND n.nspname = 'public') THEN
    CREATE INDEX flows_flowrun_org_id_modified_on_responded ON flows_flowrun (org_id, modified_on DESC) WHERE responded = TRUE;
END IF;

IF NOT EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relname = 'flows_flowrun_flow_id_modified_on_responded' AND n.nspname = 'public') THEN
    CREATE INDEX flows_flowrun_flow_id_modified_on_responded ON flows_flowrun (flow_id, modified_on DESC) WHERE responded = TRUE;
END IF;

END$$;"""


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0046_flowrun_responded_unnull'),
    ]

    operations = [
        migrations.RunSQL(INDEX_SQL)
    ]
