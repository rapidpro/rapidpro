# Generated by Django 2.2.24 on 2021-11-04 11:11

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("msgs", "0146_merge_20210517_1335"),
    ]

    operations = [
        migrations.AddField(
            model_name="msg",
            name="segments",
            field=models.PositiveSmallIntegerField(null=True),
        ),
    ]