# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='org',
            name='webhook_header_field_name',
            field=models.CharField(help_text='Optional header field name to include with Webhook requests', max_length=255, null=True, verbose_name='Webhook Header Field Name', blank=True),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='org',
            name='webhook_header_value',
            field=models.CharField(help_text='Optional header value to include with Webhook requests', max_length=255, null=True, verbose_name='Webhook Header Value', blank=True),
            preserve_default=True,
        ),
    ]
