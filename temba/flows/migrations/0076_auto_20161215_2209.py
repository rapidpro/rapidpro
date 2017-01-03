# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0075_auto_20161201_1536'),
    ]

    operations = [
        migrations.AlterField(
            model_name='flowpathcount',
            name='to_uuid',
            field=models.UUIDField(help_text='Which flow node they went to', null=True),
        ),
    ]
