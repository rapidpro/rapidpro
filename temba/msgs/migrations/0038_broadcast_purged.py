# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0037_backfill_recipient_counts'),
    ]

    operations = [
        migrations.AddField(
            model_name='broadcast',
            name='purged',
            field=models.BooleanField(null=True, help_text='If the messages for this broadcast have been purged'),
        ),
    ]
