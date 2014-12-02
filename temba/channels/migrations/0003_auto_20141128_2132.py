# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0002_auto_20141126_2054'),
    ]

    operations = [
        migrations.AlterField(
            model_name='channellog',
            name='is_error',
            field=models.BooleanField(default=None),
            preserve_default=True,
        ),
    ]
