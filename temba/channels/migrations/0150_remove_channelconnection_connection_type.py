# Generated by Django 4.0.7 on 2022-09-19 17:35

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("channels", "0149_remove_channellog_description_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="channelconnection",
            name="connection_type",
        ),
    ]
