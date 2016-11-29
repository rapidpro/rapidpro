# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from temba.sql import InstallSQL

class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0037_failed_to_stopped'),
    ]

    operations = [
        InstallSQL('0038_contacts')
    ]
