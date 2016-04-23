# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from temba.sql import InstallSQL

class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0048_auto_20160308_2131'),
    ]

    operations = [
        InstallSQL('0049_msgs')
    ]
