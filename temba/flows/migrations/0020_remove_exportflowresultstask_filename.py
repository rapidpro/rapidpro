# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0019_auto_20150420_1701'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='exportflowresultstask',
            name='filename',
        ),
    ]
