# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
import temba.utils.models


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0004_auto_20141205_1411'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contact',
            name='uuid',
            field=models.CharField(default=temba.utils.models.generate_uuid, max_length=36, help_text='The unique identifier for this object', unique=True, verbose_name='Unique Identifier', db_index=True),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='contactgroup',
            name='uuid',
            field=models.CharField(default=temba.utils.models.generate_uuid, max_length=36, help_text='The unique identifier for this object', unique=True, verbose_name='Unique Identifier', db_index=True),
            preserve_default=True,
        ),
    ]
