# Generated by Django 4.2.3 on 2023-10-04 15:54

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("msgs", "0250_alter_msg_msg_type"),
        ("channels", "0176_remove_channel_alert_email_delete_alert"),
    ]

    operations = [
        migrations.AddField(
            model_name="channelevent",
            name="optin",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, related_name="optins", to="msgs.optin"
            ),
        ),
    ]