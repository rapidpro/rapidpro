# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from temba.sql import InstallSQL


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0025_auto_20160902_1601'),
    ]

    operations = [
        InstallSQL('0026_orgs')
    ]
