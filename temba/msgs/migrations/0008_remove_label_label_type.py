# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0007_auto_20150312_1024'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='label',
            name='label_type',
        ),
    ]
