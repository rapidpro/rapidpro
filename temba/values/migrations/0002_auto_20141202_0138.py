# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('values', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='value',
            name='rule_uuid',
            field=models.CharField(help_text=b'The rule that matched, only appropriate for RuleSet values', max_length=255, null=True, db_index=True),
            preserve_default=True,
        ),
    ]
