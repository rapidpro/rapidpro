# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


def set_twitter_channel_schemes(apps, schema_editor):
    Channel = apps.get_model('channels', 'Channel')
    Channel.objects.filter(channel_type='TT').update(scheme='twitter')


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0025_auto_20151021_1619'),
    ]

    operations = [
        migrations.AddField(
            model_name='channel',
            name='scheme',
            field=models.CharField(default='tel', help_text='The URN scheme this channel can handle', max_length=8, verbose_name='URN Scheme'),
            preserve_default=True,
        ),
        migrations.RunPython(set_twitter_channel_schemes)
    ]
