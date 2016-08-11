# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import mptt.fields


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0004_auto_20151120_0848'),
    ]

    operations = [
        migrations.AlterField(
            model_name='adminboundary',
            name='level',
            field=models.IntegerField(help_text='The level of the boundary, 0 for country, 1 for state, 2 for district, 3 for ward'),
        ),
        migrations.AlterField(
            model_name='adminboundary',
            name='parent',
            field=mptt.fields.TreeForeignKey(related_name='children', blank=True, to='locations.AdminBoundary', help_text='The parent to this political boundary if any', null=True),
        ),
    ]
