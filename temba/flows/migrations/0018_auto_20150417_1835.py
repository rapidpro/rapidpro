# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0017_auto_20150401_2203'),
    ]

    operations = [
        migrations.AlterField(
            model_name='flowstep',
            name='arrived_on',
            field=models.DateTimeField(help_text='When the user arrived at this step in the flow'),
            preserve_default=True,
        ),
    ]
