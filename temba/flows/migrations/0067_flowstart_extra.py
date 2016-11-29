# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0066_auto_20160816_1909'),
    ]

    operations = [
        migrations.AddField(
            model_name='flowstart',
            name='extra',
            field=models.TextField(help_text='Any extra parameters to pass to the flow start (json)', null=True),
        ),
    ]
