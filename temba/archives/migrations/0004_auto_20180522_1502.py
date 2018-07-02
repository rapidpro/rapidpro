# -*- coding: utf-8 -*-
# Generated by Django 1.11.6 on 2018-05-22 15:02
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("archives", "0003_auto_20180517_1812")]

    operations = [
        migrations.AlterField(
            model_name="archive",
            name="archive_type",
            field=models.CharField(
                choices=[("message", "Message"), ("run", "Run")],
                help_text="The type of record this is an archive for",
                max_length=16,
            ),
        )
    ]
