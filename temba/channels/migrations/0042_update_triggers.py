# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from temba.sql import InstallSQL


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0041_auto_20161117_2027'),
    ]

    operations = [
        InstallSQL('0042_channels')
    ]
