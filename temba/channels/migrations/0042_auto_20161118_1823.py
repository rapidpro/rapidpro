# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0041_auto_20161117_1616'),
    ]

    operations = [
        migrations.AlterField(
            model_name='channelsession',
            name='session_type',
            field=models.CharField(default='F', help_text='What sort of session this is', max_length=1, choices=[('F', 'Flow')]),
        ),
    ]
