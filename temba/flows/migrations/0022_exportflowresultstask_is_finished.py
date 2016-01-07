# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0021_actionset_destination_uuid'),
    ]

    operations = [
        migrations.AddField(
            model_name='exportflowresultstask',
            name='is_finished',
            field=models.BooleanField(default=False, help_text='Whether this export is complete'),
            preserve_default=True,
        ),
        migrations.RunSQL("UPDATE flows_exportflowresultstask SET is_finished=TRUE;"),
    ]
