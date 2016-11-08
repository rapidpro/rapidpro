# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from temba.sql import InstallSQL


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0039_channellog_request_time'),
    ]

    operations = [
        InstallSQL('0040_channels')
    ]
