# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0003_auto_20141126_2054'),
    ]

    operations = [
        migrations.AddField(
            model_name='webhookresult',
            name='request',
            field=models.TextField(help_text='The request that was posted to the webhook', null=True, blank=True),
            preserve_default=True,
        ),
    ]
