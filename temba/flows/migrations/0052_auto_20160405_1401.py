# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0051_auto_20160203_2203'),
    ]

    operations = [
        migrations.AddField(
            model_name='exportflowresultstask',
            name='config',
            field=models.TextField(help_text='Any configuration options for this flow export', null=True),
        ),
    ]
