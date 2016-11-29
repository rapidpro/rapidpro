# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0069_auto_20160831_1731'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='exportflowresultstask',
            name='host',
        ),
    ]
