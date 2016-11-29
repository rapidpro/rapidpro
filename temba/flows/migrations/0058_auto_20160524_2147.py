# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0057_flowrun_parent'),
    ]

    operations = [
        migrations.AlterField(
            model_name='flow',
            name='version_number',
            field=models.IntegerField(default=9, help_text='The flow version this definition is in'),
        ),
        migrations.AlterField(
            model_name='flowrevision',
            name='spec_version',
            field=models.IntegerField(default=9, help_text='The flow version this definition is in'),
        ),
    ]
