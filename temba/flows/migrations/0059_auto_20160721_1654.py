# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models

INDEX_SQL = """
CREATE INDEX flows_flowrun_parent_created_on_not_null
ON flows_flowrun (parent_id, created_on desc) WHERE parent_id IS NOT NULL;
"""

class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0058_auto_20160524_2147'),
    ]

    operations = [
        migrations.RunSQL(INDEX_SQL)
    ]
