# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0024_flowversion_version_number'),
    ]

    operations = [
        migrations.AlterField(
            model_name='flowversion',
            name='version_number',
            field=models.IntegerField(default=5, help_text='The flow version this definition is in'),
            preserve_default=True,
        ),
    ]
