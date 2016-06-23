# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from temba.sql import InstallSQL


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0057_update_triggers'),
    ]

    operations = [
        InstallSQL("0058_msgs")
    ]
