# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0027_update_triggers'),
    ]

    operations = [
        migrations.AddField(
            model_name='org',
            name='is_purgeable',
            field=models.BooleanField(default=False, help_text="Whether this org's outgoing messages should be purged"),
        ),
    ]
