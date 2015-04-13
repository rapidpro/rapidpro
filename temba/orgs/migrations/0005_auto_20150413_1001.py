# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0004_merge'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='org',
            name='webhook_header_field_name',
        ),
        migrations.RemoveField(
            model_name='org',
            name='webhook_header_value',
        ),
        migrations.AlterField(
            model_name='org',
            name='webhook',
            field=models.TextField(help_text='Webhook endpoint and configuration', null=True, verbose_name='Webhook'),
            preserve_default=True,
        ),
    ]
