# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0034_auto_20160823_1616'),
    ]

    operations = [
        migrations.AlterField(
            model_name='channellog',
            name='msg',
            field=models.ForeignKey(related_name='channel_logs', to='msgs.Msg', help_text='The message that was sent'),
        ),
    ]
