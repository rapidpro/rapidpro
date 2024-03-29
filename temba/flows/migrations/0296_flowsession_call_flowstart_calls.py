# Generated by Django 4.0.7 on 2022-09-20 17:27

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ivr", "0020_add_call"),
        ("flows", "0295_alter_exportflowresultstask_uuid_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="flowsession",
            name="call",
            field=models.OneToOneField(
                null=True, on_delete=django.db.models.deletion.PROTECT, related_name="session", to="ivr.call"
            ),
        ),
        migrations.AddField(
            model_name="flowstart",
            name="calls",
            field=models.ManyToManyField(related_name="starts", to="ivr.call"),
        ),
    ]
