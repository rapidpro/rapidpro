# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0068_fix_empty_flow_starts'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='exportflowresultstask',
            name='host',
        ),
    ]
