# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0005_auto_20141216_0629'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='flowrun',
            name='uuid',
        ),
    ]
