# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from temba.sql import InstallSQL

class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0015_auto_20151027_1248'),
    ]

    operations = [
        InstallSQL('0016_orgs')
    ]
