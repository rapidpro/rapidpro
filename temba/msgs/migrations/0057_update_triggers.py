# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from temba.sql import InstallSQL


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0056_delete_call'),
    ]

    operations = [
        InstallSQL("0057_msgs")
    ]
