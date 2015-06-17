# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


def update_pending_messages(apps, schema_editor):
    Msg = apps.get_model('msgs', 'Msg')
    Msg.objects.filter(direction='I', status='P', msg_type='I').update(msg_type=None)


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0018_msg_type_nullable'),
    ]

    operations = [
        migrations.RunPython(update_pending_messages)
    ]
