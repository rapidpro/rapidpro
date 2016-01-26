# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0014_auto_20151027_1242'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='creditalert',
            name='threshold',
        ),
        migrations.AlterField(
            model_name='creditalert',
            name='alert_type',
            field=models.CharField(default='L', help_text='The type of this alert', max_length=1, choices=[('O', 'Credits Over'), ('L', 'Low Credits'), ('E', 'Credits expiring soon')]),
            preserve_default=False,
        ),
    ]
