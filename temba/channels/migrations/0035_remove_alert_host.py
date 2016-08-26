# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0034_auto_20160823_1616'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='alert',
            name='host',
        ),
    ]
