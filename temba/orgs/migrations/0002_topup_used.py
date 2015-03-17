# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='topup',
            name='used',
            field=models.IntegerField(default=0, help_text='The number of credits used in this top up', verbose_name='Number of Credits used'),
            preserve_default=True,
        ),
    ]
