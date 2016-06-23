# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0051_auto_20160414_1504'),
    ]

    operations = [
        migrations.AlterField(
            model_name='msg',
            name='media',
            field=models.URLField(help_text='The media associated with this message if any', max_length=255, null=True, blank=True),
        ),
    ]
