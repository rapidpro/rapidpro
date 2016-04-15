# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('values', '0006_auto_20160224_1904'),
    ]

    operations = [
        migrations.AlterField(
            model_name='value',
            name='media_value',
            field=models.TextField(help_text='The media value if any.', max_length=640, null=True),
        ),
    ]
