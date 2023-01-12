# Generated by Django 4.0.8 on 2023-01-06 15:45

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flows", "0307_alter_flow_base_language"),
    ]

    operations = [
        migrations.AlterField(
            model_name="flow",
            name="base_language",
            field=models.CharField(
                default="eng",
                help_text="The authoring language, additional languages can be added later.",
                max_length=4,
            ),
        ),
        migrations.AlterField(
            model_name="flow",
            name="expires_after_minutes",
            field=models.IntegerField(
                default=10080, help_text="Minutes of inactivity that will cause expiration from flow."
            ),
        ),
        migrations.AlterField(
            model_name="flow",
            name="ignore_triggers",
            field=models.BooleanField(default=False, help_text="Ignore keyword triggers while in this flow."),
        ),
        migrations.AlterField(
            model_name="flow",
            name="version_number",
            field=models.CharField(default="0.0.0", max_length=8),
        ),
    ]