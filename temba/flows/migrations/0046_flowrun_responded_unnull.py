# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0045_populate_responded'),
    ]

    operations = [
        migrations.AlterField(
            model_name='flowrun',
            name='responded',
            field=models.BooleanField(default=False, help_text='Whether contact has responded in this run'),
        ),
    ]
