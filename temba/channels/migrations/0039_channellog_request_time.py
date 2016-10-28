# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0038_auto_20161004_1538'),
    ]

    operations = [
        migrations.AddField(
            model_name='channellog',
            name='request_time',
            field=models.IntegerField(help_text='Time it took to process this request', null=True),
        ),
    ]
