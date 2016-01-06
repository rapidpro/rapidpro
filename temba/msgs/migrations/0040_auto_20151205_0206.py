# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations
from . import update_msg_purge_status


def update_purge(apps, schema_editor):

    # we want to be non-atomic
    if schema_editor.connection.in_atomic_block:
            schema_editor.atomic.__exit__(None, None, None)
    update_msg_purge_status(apps.get_model('msgs', 'Broadcast'), apps.get_model('msgs', 'Msg'))

class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0039_auto_20151204_2238'),
    ]

    operations = [
        migrations.RunPython(update_purge, atomic=False),
    ]
