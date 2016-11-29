# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from temba.sql import InstallSQL


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0045_auto_20161128_1450'),
    ]

    operations = [
        InstallSQL('0046_channels')
    ]
