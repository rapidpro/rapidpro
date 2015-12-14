# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0040_auto_20151103_1014'),
    ]

    operations = [
        migrations.RunSQL('CREATE INDEX "flows_flowrun_flow_id_modified_on" ON flows_flowrun (flow_id, modified_on DESC);'),
        migrations.RunSQL('CREATE INDEX "flows_flowrun_null_expired_on" ON flows_flowrun (expired_on) WHERE expired_on IS NULL;'),
        migrations.RunSQL('CREATE INDEX "flows_flowrun_org_id_modified_on" ON flows_flowrun (org_id, modified_on DESC);')
    ]
