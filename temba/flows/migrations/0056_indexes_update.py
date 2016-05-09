# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations


INDEX_SQL = """
CREATE INDEX flows_flowrun_org_modified_id
ON flows_flowrun (org_id, modified_on DESC, id DESC);

DROP INDEX IF EXISTS flows_flowrun_org_id_modified_on;

CREATE INDEX flows_flowrun_org_responded_modified_id
ON flows_flowrun (org_id, responded, modified_on DESC, id DESC);

DROP INDEX IF EXISTS flows_flowrun_org_id_modified_on_responded;

CREATE INDEX flows_flowrun_flow_modified_id
ON flows_flowrun (flow_id, modified_on DESC, id DESC);

DROP INDEX IF EXISTS flows_flowrun_flow_id_modified_on;

CREATE INDEX flows_flowrun_flow_responded_modified_id
ON flows_flowrun (flow_id, responded, modified_on DESC, id DESC);

DROP INDEX IF EXISTS flows_flowrun_flow_id_modified_on_responded;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0055_populate_step_broadcasts'),
    ]

    operations = [
        migrations.RunSQL(INDEX_SQL)
    ]
