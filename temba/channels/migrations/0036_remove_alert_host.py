# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0035_auto_20160822_2103'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='alert',
            name='host',
        ),
    ]
