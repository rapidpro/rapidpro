# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from temba.sql import InstallSQL


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0041_auto_20151207_1936'),
    ]

    operations = [
        InstallSQL('0042_msgs')
    ]
