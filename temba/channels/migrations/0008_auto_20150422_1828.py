# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations

def update_twitter_channel_name(apps, schema_editor):
    Channel = apps.get_model('channels', 'Channel')
    for channel in Channel.objects.filter(channel_type='TT', name="Twitter"):
        channel.name = '@%s' % channel.address
        channel.save()


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0007_auto_20150402_2103'),
    ]

    operations = [
        migrations.RunPython(update_twitter_channel_name)
    ]
