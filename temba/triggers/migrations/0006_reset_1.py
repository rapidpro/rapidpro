import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("flows", "0079_reset_1"),
        ("schedules", "0003_reset_1"),
        ("orgs", "0029_reset_1"),
        ("channels", "0052_reset_3"),
        ("contacts", "0046_reset_1"),
    ]

    operations = [
        migrations.CreateModel(
            name="Trigger",
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
                    "keyword",
                    models.CharField(
                        blank=True,
                        help_text="The first word in the message text",
                        max_length=16,
                        null=True,
                        verbose_name="Keyword",
                    ),
                ),
                (
                    "last_triggered",
                    models.DateTimeField(
                        default=None,
                        help_text="The last time this trigger was fired",
                        null=True,
                        verbose_name="Last Triggered",
                    ),
                ),
                (
                    "trigger_count",
                    models.IntegerField(
                        default=0, help_text="How many times this trigger has fired", verbose_name="Trigger Count"
                    ),
                ),
                (
                    "is_archived",
                    models.BooleanField(
                        default=False, help_text="Whether this trigger is archived", verbose_name="Is Archived"
                    ),
                ),
                (
                    "trigger_type",
                    models.CharField(
                        choices=[
                            ("K", "Keyword Trigger"),
                            ("S", "Schedule Trigger"),
                            ("V", "Inbound Call Trigger"),
                            ("M", "Missed Call Trigger"),
                            ("C", "Catch All Trigger"),
                            ("F", "Follow Account Trigger"),
                            ("N", "New Conversation Trigger"),
                            ("U", "USSD Pull Session Trigger"),
                        ],
                        default="K",
                        help_text="The type of this trigger",
                        max_length=1,
                        verbose_name="Trigger Type",
                    ),
                ),
                (
                    "channel",
                    models.OneToOneField(
                        help_text="The associated channel",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="channels.Channel",
                        verbose_name="Channel",
                    ),
                ),
                (
                    "contacts",
                    models.ManyToManyField(
                        help_text="Individual contacts to broadcast the flow to",
                        to="contacts.Contact",
                        verbose_name="Contacts",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        help_text="The user which originally created this item",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="triggers_trigger_creations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "flow",
                    models.ForeignKey(
                        help_text="Which flow will be started",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="triggers",
                        to="flows.Flow",
                        verbose_name="Flow",
                    ),
                ),
                (
                    "groups",
                    models.ManyToManyField(
                        help_text="The groups to broadcast the flow to",
                        to="contacts.ContactGroup",
                        verbose_name="Groups",
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        help_text="The user which last modified this item",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="triggers_trigger_modifications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "org",
                    models.ForeignKey(
                        help_text="The organization this trigger belongs to",
                        on_delete=django.db.models.deletion.CASCADE,
                        to="orgs.Org",
                        verbose_name="Org",
                    ),
                ),
                (
                    "schedule",
                    models.OneToOneField(
                        blank=True,
                        help_text="Our recurring schedule",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="trigger",
                        to="schedules.Schedule",
                        verbose_name="Schedule",
                    ),
                ),
            ],
            options={"abstract": False},
        )
    ]
