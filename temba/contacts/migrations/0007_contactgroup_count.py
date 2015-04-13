# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0006_reorganize_exports')
    ]

    operations = [
        migrations.AddField(
            model_name='contactgroup',
            name='count',
            field=models.IntegerField(default=0, help_text='The number of contacts in this group', verbose_name='Count'),
            preserve_default=True,
        ),
    ]
