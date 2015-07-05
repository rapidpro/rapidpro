# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0011_auto_20150703_1836'),
    ]

    def populate_channels(apps, schema_editor):
        ChannelLog = apps.get_model('channels', 'ChannelLog')

        # for each unique channel log element
        channels = ChannelLog.objects.all().values('msg__channel').order_by('msg__channel').distinct()
        for channel in channels:
            print("Updating channel %d" % channel['msg__channel'])
            ChannelLog.objects.filter(msg__channel=channel['msg__channel']).update(channel_id=channel['msg__channel'])

    operations = [
        migrations.RunPython(populate_channels),
    ]
