# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0052_auto_20160405_1401'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ruleset',
            name='value_type',
            field=models.CharField(default='T', help_text='The type of value this ruleset saves', max_length=1, choices=[('T', 'Text'), ('N', 'Numeric'), ('D', 'Date & Time'), ('S', 'State'), ('I', 'District'), ('W', 'Ward')]),
        ),
    ]
