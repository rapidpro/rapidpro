# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models

INDEX_SQL = """
CREATE INDEX flows_flowrun_timeout_active
ON flows_flowrun (timeout_on) WHERE is_active = TRUE AND timeout_on IS NOT NULL;
"""

class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0061_flowrun_timeout_on'),
    ]

    operations = [
        migrations.RunSQL(INDEX_SQL),
    ]
