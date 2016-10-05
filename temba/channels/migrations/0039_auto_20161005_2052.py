# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0038_auto_20160927_2013'),
    ]

    operations = [
        migrations.AlterField(
            model_name='channel',
            name='address',
            field=models.CharField(help_text='Address with which this channel communicates', max_length=64, null=True, verbose_name='Address', blank=True),
        ),
    ]
