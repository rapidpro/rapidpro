# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0041_auto_20161117_1616'),
        ('ivr', '0011_auto_20161111_1151'),
    ]

    operations = [
        migrations.CreateModel(
            name='IVRCall',
            fields=[
            ],
            options={
                'proxy': True,
            },
            bases=('channels.channelsession',),
        ),
    ]
