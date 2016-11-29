# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0019_org_surveyor_password'),
    ]

    operations = [
        migrations.AlterField(
            model_name='topup',
            name='price',
            field=models.IntegerField(help_text='The price paid for the messages in this top up (in cents)', null=True, verbose_name='Price Paid', blank=True),
        ),
    ]
