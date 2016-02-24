# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from temba.sql import InstallSQL

class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0029_auto_20160202_1931'),
    ]

    operations = [
        InstallSQL('0030_channels')
    ]
