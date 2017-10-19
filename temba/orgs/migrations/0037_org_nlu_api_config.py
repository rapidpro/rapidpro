# -*- coding: utf-8 -*-
# Generated by Django 1.11.2 on 2017-10-09 18:27
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0036_ensure_anon_user_exists'),
    ]

    operations = [
        migrations.AddField(
            model_name='org',
            name='nlu_api_config',
            field=models.CharField(default=None, help_text='Configurations to Natural Language Understand Api', max_length=255, null=True),
        ),
    ]
