# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0053_auto_20160415_1337'),
    ]

    operations = [
        migrations.AlterField(
            model_name='call',
            name='call_type',
            field=models.CharField(help_text='The type of call', max_length=16, verbose_name='Call Type',
                                   choices=[('unknown', 'Unknown Call Type'),
                                            ('mt_call', 'Outgoing Call'),
                                            ('mt_miss', 'Missed Outgoing Call'),
                                            ('mo_call', 'Incoming Call'),
                                            ('mo_miss', 'Missed Incoming Call')]),
        ),
    ]
