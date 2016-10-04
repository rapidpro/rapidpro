# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import temba.utils.models


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0006_auto_20150825_1953'),
    ]

    operations = [
        migrations.AlterField(
            model_name='campaign',
            name='uuid',
            field=models.CharField(default=temba.utils.models.generate_uuid, max_length=36, help_text='The unique identifier for this object', unique=True, verbose_name='Unique Identifier', db_index=True),
        ),
        migrations.AlterField(
            model_name='campaignevent',
            name='uuid',
            field=models.CharField(default=temba.utils.models.generate_uuid, max_length=36, help_text='The unique identifier for this object', unique=True, verbose_name='Unique Identifier', db_index=True),
        ),
    ]
