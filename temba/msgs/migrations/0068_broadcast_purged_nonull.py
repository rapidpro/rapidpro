# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0067_auto_20161005_0731'),
    ]

    operations = [
        migrations.AlterField(
            model_name='broadcast',
            name='purged',
            field=models.BooleanField(default=False, help_text='If the messages for this broadcast have been purged'),
        ),
    ]
