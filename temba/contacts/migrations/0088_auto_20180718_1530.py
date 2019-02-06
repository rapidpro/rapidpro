# -*- coding: utf-8 -*-
# Generated by Django 1.11.14 on 2018-07-18 15:30
from __future__ import unicode_literals

import django.db.models.manager
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("contacts", "0087_triggers")]

    operations = [
        migrations.AlterModelManagers(
            name="contactfield", managers=[("all_fields", django.db.models.manager.Manager())]
        ),
        migrations.AddField(
            model_name="contactfield",
            name="field_type",
            field=models.CharField(choices=[("S", "System"), ("U", "User")], default="U", max_length=1),
        ),
    ]
