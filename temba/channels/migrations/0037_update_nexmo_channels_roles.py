# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0036_remove_alert_host'),
    ]

    def update_nexmo_channles_roles(apps, schema_editor):
        Channel = apps.get_model('channels', 'Channel')
        Channel.objects.filter(channel_type='NX').update(role='SRCA')


    operations = [
        migrations.RunPython(update_nexmo_channles_roles)
    ]
