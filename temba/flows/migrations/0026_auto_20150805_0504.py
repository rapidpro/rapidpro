# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0025_create_flow_expiration_index'),
    ]

    operations = [
        migrations.AddField(
            model_name='ruleset',
            name='config',
            field=models.TextField(help_text='RuleSet type specific configuration', null=True, verbose_name='Ruleset Configuration'),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='flow',
            name='version_number',
            field=models.IntegerField(default=5, help_text='The flow version this definition is in'),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='flowversion',
            name='version_number',
            field=models.IntegerField(default=5, help_text='The flow version this definition is in'),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='ruleset',
            name='response_type',
            field=models.CharField(help_text='The type of response that is being saved', max_length=1),
            preserve_default=True,
        ),
    ]
