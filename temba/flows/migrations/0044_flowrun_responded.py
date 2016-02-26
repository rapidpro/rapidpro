# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0043_populate_exit_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='flowrun',
            name='responded',
            field=models.NullBooleanField(help_text='Whether contact has responded in this run'),
        ),
    ]
