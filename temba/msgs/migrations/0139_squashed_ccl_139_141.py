# Generated by Django 2.2.4 on 2020-12-11 12:44

from django.db import migrations
import temba.utils.models


class Migration(migrations.Migration):

    replaces = [
        ("msgs", "0139_merge_20200313_1155"),
        ("msgs", "0140_auto_20200317_1558"),
        ("msgs", "0141_auto_20200402_1534"),
    ]

    dependencies = [("msgs", "0138_remove_broadcast_recipient_count"), ("msgs", "0133_auto_20191122_0154")]

    operations = [
        migrations.AlterField(
            model_name="broadcast",
            name="text",
            field=temba.utils.models.TranslatableField(
                help_text="The localized versions of the message text", max_length=640, verbose_name="Translations"
            ),
        )
    ]