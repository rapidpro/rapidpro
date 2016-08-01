# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0060_exit_flowruns'),
    ]

    operations = [
        migrations.AddField(
            model_name='flowrun',
            name='timeout_on',
            field=models.DateTimeField(help_text='When this flow will next time out (if any)', null=True),
        ),
    ]
