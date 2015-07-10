# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0020_update_label_triggers'),
    ]

    operations = [
        migrations.AddField(
            model_name='exportmessagestask',
            name='is_finished',
            field=models.BooleanField(default=False, help_text='Whether this export is finished running'),
            preserve_default=True,
        ),
        migrations.RunSQL("UPDATE msgs_exportmessagestask SET is_finished=TRUE;"),
    ]
