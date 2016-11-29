# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0059_indexes_update'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='broadcast',
            name='recipients',
        ),
    ]
