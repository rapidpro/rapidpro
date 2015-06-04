# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('values', '0002_auto_20141202_0138'),
    ]

    operations = [
        migrations.AlterField(
            model_name='value',
            name='category',
            field=models.CharField(help_text='The name of the category this value matched in the RuleSet', max_length=128, null=True),
            preserve_default=True,
        ),
    ]
