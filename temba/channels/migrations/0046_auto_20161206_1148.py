# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0045_auto_20161128_1450'),
    ]

    operations = [
        migrations.AlterField(
            model_name='channelsession',
            name='status',
            field=models.CharField(default='P', help_text='The status of this session', max_length=1, choices=[('Q', 'Queued'), ('R', 'Ringing'), ('I', 'In Progress'), ('D', 'Complete'), ('B', 'Busy'), ('F', 'Failed'), ('N', 'No Answer'), ('C', 'Canceled'), ('X', 'Interrupted'), ('T', 'Triggered'), ('A', 'Initiated')]),
        ),
    ]
