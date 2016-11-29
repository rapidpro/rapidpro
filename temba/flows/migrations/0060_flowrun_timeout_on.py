# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models

INDEX_SQL = """
CREATE INDEX flows_flowrun_timeout_active
ON flows_flowrun (timeout_on) WHERE is_active = TRUE AND timeout_on IS NOT NULL;
"""

class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0059_auto_20160721_1654'),
    ]

    operations = [
        migrations.AddField(
            model_name='flowrun',
            name='timeout_on',
            field=models.DateTimeField(help_text='When this flow will next time out (if any)', null=True),
        ),
        migrations.RunSQL(INDEX_SQL),
    ]
