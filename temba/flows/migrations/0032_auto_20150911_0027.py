# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0031_auto_20150901_2345'),
    ]

    operations = [
        migrations.AlterField(
            model_name='flow',
            name='base_language',
            field=models.CharField(help_text='The primary language for editing this flow', max_length=4, null=True, blank=True),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='flow',
            name='version_number',
            field=models.IntegerField(default=6, help_text='The flow version this definition is in'),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='flowversion',
            name='version_number',
            field=models.IntegerField(default=6, help_text='The flow version this definition is in'),
            preserve_default=True,
        ),
    ]
