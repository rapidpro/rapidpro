# Generated by Django 2.2.20 on 2021-06-30 19:14

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("msgs", "0151_auto_20210630_1605"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="broadcast",
            name="is_active",
        ),
    ]
