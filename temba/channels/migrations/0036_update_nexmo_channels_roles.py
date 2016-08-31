# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0035_auto_20160822_2103'),
    ]

    def update_nexmo_channles_roles(apps, schema_editor):
        Channel = apps.get_model('channels', 'Channel')
        Channel.objects.filter(channel_type='NX').update(role='SRCA')


    operations = [
        migrations.RunPython(update_nexmo_channles_roles)
    ]
