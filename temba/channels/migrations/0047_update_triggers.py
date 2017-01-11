# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from temba.sql import InstallSQL


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0046_auto_20161208_2139'),
    ]

    operations = [
        InstallSQL('0046_channels')
    ]
