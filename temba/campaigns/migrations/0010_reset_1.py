from django.db import migrations, models

import temba.utils.models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Campaign",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "is_active",
                    models.BooleanField(
                        default=True, help_text="Whether this item is active, use this instead of deleting"
                    ),
                ),
                (
                    "created_on",
                    models.DateTimeField(auto_now_add=True, help_text="When this item was originally created"),
                ),
                ("modified_on", models.DateTimeField(auto_now=True, help_text="When this item was last modified")),
                (
                    "uuid",
                    models.CharField(
                        db_index=True,
                        default=temba.utils.models.generate_uuid,
                        help_text="The unique identifier for this object",
                        max_length=36,
                        unique=True,
                        verbose_name="Unique Identifier",
                    ),
                ),
                ("name", models.CharField(help_text="The name of this campaign", max_length=255)),
                (
                    "is_archived",
                    models.BooleanField(default=False, help_text="Whether this campaign is archived or not"),
                ),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="CampaignEvent",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "is_active",
                    models.BooleanField(
                        default=True, help_text="Whether this item is active, use this instead of deleting"
                    ),
                ),
                (
                    "created_on",
                    models.DateTimeField(auto_now_add=True, help_text="When this item was originally created"),
                ),
                ("modified_on", models.DateTimeField(auto_now=True, help_text="When this item was last modified")),
                (
                    "uuid",
                    models.CharField(
                        db_index=True,
                        default=temba.utils.models.generate_uuid,
                        help_text="The unique identifier for this object",
                        max_length=36,
                        unique=True,
                        verbose_name="Unique Identifier",
                    ),
                ),
                (
                    "offset",
                    models.IntegerField(
                        default=0, help_text="The offset in days from our date (positive is after, negative is before)"
                    ),
                ),
                (
                    "unit",
                    models.CharField(
                        choices=[("M", "Minutes"), ("H", "Hours"), ("D", "Days"), ("W", "Weeks")],
                        default="D",
                        help_text="The unit for the offset for this event",
                        max_length=1,
                    ),
                ),
                (
                    "event_type",
                    models.CharField(
                        choices=[("F", "Flow Event"), ("M", "Message Event")],
                        default="F",
                        help_text="The type of this event",
                        max_length=1,
                    ),
                ),
                ("message", models.TextField(blank=True, help_text="The message to send out", null=True)),
                (
                    "delivery_hour",
                    models.IntegerField(default=-1, help_text="The hour to send the message or flow at."),
                ),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="EventFire",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("scheduled", models.DateTimeField(help_text="When this event is scheduled to run")),
                (
                    "fired",
                    models.DateTimeField(
                        blank=True, help_text="When this event actually fired, null if not yet fired", null=True
                    ),
                ),
            ],
            options={"ordering": ("scheduled",)},
        ),
    ]
