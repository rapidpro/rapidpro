# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0024_auto_20150617_2025'),
    ]

    operations = [
        migrations.AddField(
            model_name='flowversion',
            name='version_number',
            field=models.IntegerField(help_text='The flow version this definition is in', null=True),
            preserve_default=True,
        ),
        migrations.RunSQL(
            sql='update flows_flowversion set version_number=4',
            reverse_sql='update flows_flowversion set version_number=null',
        ),
        migrations.AlterField(
            model_name='flowversion',
            name='version_number',
            field=models.IntegerField(default=4, help_text='The flow version this definition is in'),
            preserve_default=True,
        ),
    ]
