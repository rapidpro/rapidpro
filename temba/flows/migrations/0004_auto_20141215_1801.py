# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import temba.utils.models

from django.db import models, migrations
from uuid import uuid4


def populate_flowrun_uuid(apps, schema_editor):
    model = apps.get_model("flows", "FlowRun")
    for obj in model.objects.filter(uuid=None):
        obj.uuid = uuid4()
        obj.save(update_fields=('uuid',))


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0003_auto_20141210_0208'),
    ]

    operations = [
        migrations.RunPython(
            populate_flowrun_uuid
        ),
        migrations.AlterField(
            model_name='flowrun',
            name='uuid',
            field=models.CharField(default=temba.utils.models.generate_uuid, max_length=36, help_text='The unique identifier for this object', unique=True, verbose_name='Unique Identifier', db_index=True),
            preserve_default=True,
        ),
    ]
