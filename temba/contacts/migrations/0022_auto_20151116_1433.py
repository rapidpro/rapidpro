# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0021_auto_20150727_0727'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contactgroup',
            name='name',
            field=models.CharField(help_text='The name of this contact group', max_length=64, verbose_name='Name'),
            preserve_default=True,
        ),
    ]
