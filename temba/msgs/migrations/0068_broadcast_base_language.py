# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0067_auto_20161005_0731'),
    ]

    operations = [
        migrations.AddField(
            model_name='broadcast',
            name='base_language',
            field=models.CharField(max_length=4, null=True, help_text='The language used to send this to contacts without a language', blank=True),
        ),
    ]
