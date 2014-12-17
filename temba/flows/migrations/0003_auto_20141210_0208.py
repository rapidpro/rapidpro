# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from uuid import uuid4

import temba.utils.models


def populate_flow_uuid(apps, schema_editor):
    model = apps.get_model("flows", "Flow")
    for obj in model.objects.all():
        obj.uuid = uuid4()
        obj.save(update_fields=('uuid',))


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0002_auto_20141126_2054'),
    ]

    operations = [
        migrations.AddField(
            model_name='flow',
            name='uuid',
            field=models.CharField(null=True, max_length=36, help_text='The unique identifier for this object', verbose_name='Unique Identifier'),
            preserve_default=True,
        ),
        migrations.RunPython(
            populate_flow_uuid
        ),
        migrations.AlterField(
            model_name='flow',
            name='uuid',
            field=models.CharField(default=temba.utils.models.generate_uuid, max_length=36, help_text='The unique identifier for this object', unique=True, verbose_name='Unique Identifier', db_index=True),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='flowrun',
            name='uuid',
            field=models.CharField(help_text='The unique identifier for this object', max_length=36, null=True, verbose_name='Unique Identifier'),
            preserve_default=True,
        ),
    ]
