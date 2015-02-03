# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('ivr', '0006_auto_20150203_0558'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='ivraction',
            name='call',
        ),
        migrations.RemoveField(
            model_name='ivraction',
            name='org',
        ),
        migrations.RemoveField(
            model_name='ivraction',
            name='step',
        ),
        migrations.RemoveField(
            model_name='ivraction',
            name='topup',
        ),
        migrations.DeleteModel(
            name='IVRAction',
        ),
    ]
