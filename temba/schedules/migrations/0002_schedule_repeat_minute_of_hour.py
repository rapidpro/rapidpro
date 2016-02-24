# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('schedules', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='schedule',
            name='repeat_minute_of_hour',
            field=models.IntegerField(help_text='The minute of the hour', null=True),
        ),
    ]
