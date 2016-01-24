# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0012_auto_20151026_1152'),
    ]

    operations = [
        migrations.AddField(
            model_name='creditalert',
            name='alert_type',
            field=models.CharField(help_text='The type of this alert', max_length=1, null=True, choices=[('O', 'Credits Over'), ('L', 'Low Credits'), ('E', 'Credits expiring soon')]),
            preserve_default=True,
        ),
    ]
