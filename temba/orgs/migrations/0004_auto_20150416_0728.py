# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0003_auto_20150313_1624'),
    ]

    operations = [
        migrations.AlterField(
            model_name='org',
            name='webhook',
            field=models.TextField(help_text='Webhook endpoint and configuration', null=True, verbose_name='Webhook'),
            preserve_default=True,
        ),
    ]
