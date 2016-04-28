# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0055_auto_20160427_1937'),
        ('channels', '0032_channelevent'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='call',
            name='channel',
        ),
        migrations.RemoveField(
            model_name='call',
            name='contact',
        ),
        migrations.RemoveField(
            model_name='call',
            name='created_by',
        ),
        migrations.RemoveField(
            model_name='call',
            name='modified_by',
        ),
        migrations.RemoveField(
            model_name='call',
            name='org',
        ),
        migrations.DeleteModel(
            name='Call',
        ),
    ]
