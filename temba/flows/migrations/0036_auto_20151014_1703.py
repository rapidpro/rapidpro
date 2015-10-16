# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0035_auto_20151001_1831'),
    ]

    operations = [
        migrations.AddField(
            model_name='exportflowresultstask',
            name='uuid',
            field=models.CharField(help_text='The uuid used to name the resulting export file', max_length=36, null=True),
            preserve_default=True,
        ),
    ]
