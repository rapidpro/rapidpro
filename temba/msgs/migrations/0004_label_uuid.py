# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0003_auto_20150129_0515'),
    ]

    operations = [
        migrations.AddField(
            model_name='label',
            name='uuid',
            field=models.CharField(max_length=36, null=True),
            preserve_default=True,
        ),
    ]
