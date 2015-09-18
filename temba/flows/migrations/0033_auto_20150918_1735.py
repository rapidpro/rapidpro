# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0032_auto_20150911_0027'),
    ]

    operations = [
        migrations.AlterField(
            model_name='flow',
            name='base_language',
            field=models.CharField(default='base', max_length=4, null=True, help_text='The primary language for editing this flow', blank=True),
            preserve_default=True,
        ),
    ]
