# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from temba.sql import InstallSQL


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0021_auto_20160815_1725'),
    ]

    operations = [
        InstallSQL("0022_orgs")
    ]
