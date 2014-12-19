# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0003_contactgroup_uuid'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contact',
            name='uuid',
            field=models.CharField(help_text='The unique identifier for this contact.', unique=True, max_length=36, verbose_name='Unique Identifier', db_index=True),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='contactgroup',
            name='uuid',
            field=models.CharField(null=True, max_length=36, help_text='The unique identifier for this contact.', unique=True, verbose_name='Unique Identifier', db_index=True),
            preserve_default=True,
        ),
    ]
