# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0005_auto_20150313_1039'),
    ]

    operations = [
        migrations.AddField(
            model_name='channel',
            name='bod',
            field=models.TextField(help_text='Any channel specific state data', null=True, verbose_name='Optional Data'),
            preserve_default=True,
        ),
    ]
