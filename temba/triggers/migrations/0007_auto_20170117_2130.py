import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("triggers", "0006_reset_1")]

    operations = [
        migrations.AddField(
            model_name="trigger",
            name="referrer_id",
            field=models.CharField(
                blank=True,
                help_text="The referrer id that triggers us",
                max_length=255,
                null=True,
                verbose_name="Referrer Id",
            ),
        ),
        migrations.AlterField(
            model_name="trigger",
            name="channel",
            field=models.ForeignKey(
                help_text="The associated channel",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="triggers",
                to="channels.Channel",
                verbose_name="Channel",
            ),
        ),
        migrations.AlterField(
            model_name="trigger",
            name="trigger_type",
            field=models.CharField(
                choices=[
                    ("K", "Keyword Trigger"),
                    ("S", "Schedule Trigger"),
                    ("V", "Inbound Call Trigger"),
                    ("M", "Missed Call Trigger"),
                    ("C", "Catch All Trigger"),
                    ("F", "Follow Account Trigger"),
                    ("N", "New Conversation Trigger"),
                    ("U", "USSD Pull Session Trigger"),
                    ("R", "Referral Trigger"),
                ],
                default="K",
                help_text="The type of this trigger",
                max_length=1,
                verbose_name="Trigger Type",
            ),
        ),
    ]
