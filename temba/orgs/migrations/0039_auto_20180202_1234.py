from django.db import migrations

import temba.utils.models


class Migration(migrations.Migration):

    dependencies = [("orgs", "0038_auto_20171124_1609")]

    operations = [
        migrations.AlterField(
            model_name="org",
            name="webhook",
            field=temba.utils.models.JSONAsTextField(
                help_text="Webhook endpoint and configuration", null=True, verbose_name="Webhook", default=dict
            ),
        ),
        migrations.AlterField(
            model_name="org",
            name="config",
            field=temba.utils.models.JSONAsTextField(
                help_text="More Organization specific configuration",
                null=True,
                verbose_name="Configuration",
                default=dict,
            ),
        ),
    ]
