# Generated by Django 2.0.8 on 2018-10-18 13:38

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("channels", "0101_auto_20180828_1955")]

    operations = [
        migrations.AddField(
            model_name="channelsession",
            name="error_count",
            field=models.IntegerField(
                default=0, help_text="The number of times this call has errored", verbose_name="Error Count"
            ),
        )
    ]
