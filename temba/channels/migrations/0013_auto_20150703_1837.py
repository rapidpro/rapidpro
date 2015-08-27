# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0012_populate_channels'),
    ]

    operations = [
        migrations.AlterField(
            model_name='channellog',
            name='channel',
            field=models.ForeignKey(help_text='The channel the message was sent on', to='channels.Channel'),
            preserve_default=True,
        ),
    ]
