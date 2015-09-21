# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0033_auto_20150918_1735'),
    ]

    operations = [
        migrations.AddField(
            model_name='actionlog',
            name='level',
            field=models.CharField(default='I', help_text='Log event level', max_length=1, choices=[('I', 'Info'), ('E', 'Error')]),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='actionlog',
            name='created_on',
            field=models.DateTimeField(help_text='When this log event occurred', auto_now_add=True),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='actionlog',
            name='text',
            field=models.TextField(help_text='Log event text'),
            preserve_default=True,
        ),
    ]
