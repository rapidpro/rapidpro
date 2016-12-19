# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0071_broadcast_recipients_through'),
    ]

    operations = [
        migrations.AddField(
            model_name='broadcastrecipient',
            name='purged_status',
            field=models.CharField(help_text="Used when broadcast is purged to record contact's message's state", max_length=1, null=True),
        ),
    ]
