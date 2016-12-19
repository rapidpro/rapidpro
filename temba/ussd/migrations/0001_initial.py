# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0048_auto_20161206_1148'),
    ]

    operations = [
        migrations.CreateModel(
            name='USSDSession',
            fields=[
            ],
            options={
                'proxy': True,
            },
            bases=('channels.channelsession',),
        ),
    ]
