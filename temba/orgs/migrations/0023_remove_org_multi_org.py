# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0022_auto_20160815_1726'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='org',
            name='multi_org',
        ),
    ]
