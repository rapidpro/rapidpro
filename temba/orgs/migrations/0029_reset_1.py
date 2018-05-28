import timezone_field.fields

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [("locations", "0006_reset_1"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="CreditAlert",
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
                    "alert_type",
                    models.CharField(
                        choices=[("O", "Credits Over"), ("L", "Low Credits"), ("E", "Credits expiring soon")],
                        help_text="The type of this alert",
                        max_length=1,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        help_text="The user which originally created this item",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orgs_creditalert_creations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        help_text="The user which last modified this item",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orgs_creditalert_modifications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="Debit",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.IntegerField(help_text="How many credits were debited")),
                (
                    "debit_type",
                    models.CharField(
                        choices=[("A", "Allocation"), ("P", "Purge")], help_text="What caused this debit", max_length=1
                    ),
                ),
                (
                    "created_on",
                    models.DateTimeField(
                        default=django.utils.timezone.now, help_text="When this item was originally created"
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Invitation",
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
                    "email",
                    models.EmailField(
                        help_text="The email to which we send the invitation of the viewer",
                        max_length=254,
                        verbose_name="Email",
                    ),
                ),
                (
                    "secret",
                    models.CharField(
                        help_text="a unique code associated with this invitation",
                        max_length=64,
                        unique=True,
                        verbose_name="Secret",
                    ),
                ),
                (
                    "user_group",
                    models.CharField(
                        choices=[("A", "Administrator"), ("E", "Editor"), ("V", "Viewer"), ("S", "Surveyor")],
                        default="V",
                        max_length=1,
                        verbose_name="User Role",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        help_text="The user which originally created this item",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orgs_invitation_creations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        help_text="The user which last modified this item",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orgs_invitation_modifications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="Language",
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
                ("name", models.CharField(max_length=128)),
                ("iso_code", models.CharField(max_length=4)),
                (
                    "created_by",
                    models.ForeignKey(
                        help_text="The user which originally created this item",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orgs_language_creations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        help_text="The user which last modified this item",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orgs_language_modifications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="Org",
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
                ("name", models.CharField(max_length=128, verbose_name="Name")),
                (
                    "plan",
                    models.CharField(
                        choices=[
                            ("FREE", "Free Plan"),
                            ("TRIAL", "Trial"),
                            ("TIER_39", "Bronze"),
                            ("TIER1", "Silver"),
                            ("TIER2", "Gold (Legacy)"),
                            ("TIER3", "Platinum (Legacy)"),
                            ("TIER_249", "Gold"),
                            ("TIER_449", "Platinum"),
                        ],
                        default="FREE",
                        help_text="What plan your organization is on",
                        max_length=16,
                        verbose_name="Plan",
                    ),
                ),
                (
                    "plan_start",
                    models.DateTimeField(
                        auto_now_add=True, help_text="When the user switched to this plan", verbose_name="Plan Start"
                    ),
                ),
                (
                    "stripe_customer",
                    models.CharField(
                        blank=True,
                        help_text="Our Stripe customer id for your organization",
                        max_length=32,
                        null=True,
                        verbose_name="Stripe Customer",
                    ),
                ),
                (
                    "language",
                    models.CharField(
                        blank=True,
                        choices=[("en-us", "English"), ("pt-br", "Portuguese"), ("fr", "French"), ("es", "Spanish")],
                        help_text="The main language used by this organization",
                        max_length=64,
                        null=True,
                        verbose_name="Language",
                    ),
                ),
                ("timezone", timezone_field.fields.TimeZoneField(verbose_name="Timezone")),
                (
                    "date_format",
                    models.CharField(
                        choices=[("D", "DD-MM-YYYY"), ("M", "MM-DD-YYYY")],
                        default="D",
                        help_text="Whether day comes first or month comes first in dates",
                        max_length=1,
                        verbose_name="Date Format",
                    ),
                ),
                (
                    "webhook",
                    models.TextField(
                        help_text="Webhook endpoint and configuration", null=True, verbose_name="Webhook"
                    ),
                ),
                (
                    "webhook_events",
                    models.IntegerField(
                        default=0,
                        help_text="Which type of actions will trigger webhook events.",
                        verbose_name="Webhook Events",
                    ),
                ),
                ("msg_last_viewed", models.DateTimeField(auto_now_add=True, verbose_name="Message Last Viewed")),
                ("flows_last_viewed", models.DateTimeField(auto_now_add=True, verbose_name="Flows Last Viewed")),
                (
                    "config",
                    models.TextField(
                        help_text="More Organization specific configuration", null=True, verbose_name="Configuration"
                    ),
                ),
                (
                    "slug",
                    models.SlugField(
                        blank=True,
                        error_messages={"unique": "This slug is not available"},
                        max_length=255,
                        null=True,
                        unique=True,
                        verbose_name="Slug",
                    ),
                ),
                (
                    "is_anon",
                    models.BooleanField(
                        default=False,
                        help_text="Whether this organization anonymizes the phone numbers of contacts within it",
                    ),
                ),
                (
                    "is_purgeable",
                    models.BooleanField(
                        default=False, help_text="Whether this org's outgoing messages should be purged"
                    ),
                ),
                (
                    "brand",
                    models.CharField(
                        default="rapidpro.io",
                        help_text="The brand used in emails",
                        max_length=128,
                        verbose_name="Brand",
                    ),
                ),
                (
                    "surveyor_password",
                    models.CharField(
                        default=None,
                        help_text="A password that allows users to register as surveyors",
                        max_length=128,
                        null=True,
                    ),
                ),
                (
                    "administrators",
                    models.ManyToManyField(
                        help_text="The administrators in your organization",
                        related_name="org_admins",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Administrators",
                    ),
                ),
                (
                    "country",
                    models.ForeignKey(
                        blank=True,
                        help_text="The country this organization should map results for.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="locations.AdminBoundary",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        help_text="The user which originally created this item",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orgs_org_creations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "editors",
                    models.ManyToManyField(
                        help_text="The editors in your organization",
                        related_name="org_editors",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Editors",
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        help_text="The user which last modified this item",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orgs_org_modifications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "parent",
                    models.ForeignKey(
                        blank=True,
                        help_text="The parent org that manages this org",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="orgs.Org",
                    ),
                ),
                (
                    "primary_language",
                    models.ForeignKey(
                        blank=True,
                        help_text="The primary language will be used for contacts with no language preference.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="orgs",
                        to="orgs.Language",
                    ),
                ),
                (
                    "surveyors",
                    models.ManyToManyField(
                        help_text="The users can login via Android for your organization",
                        related_name="org_surveyors",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Surveyors",
                    ),
                ),
                (
                    "viewers",
                    models.ManyToManyField(
                        help_text="The viewers in your organization",
                        related_name="org_viewers",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Viewers",
                    ),
                ),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="TopUp",
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
                    "price",
                    models.IntegerField(
                        blank=True,
                        help_text="The price paid for the messages in this top up (in cents)",
                        null=True,
                        verbose_name="Price Paid",
                    ),
                ),
                (
                    "credits",
                    models.IntegerField(
                        help_text="The number of credits bought in this top up", verbose_name="Number of Credits"
                    ),
                ),
                (
                    "expires_on",
                    models.DateTimeField(
                        help_text="The date that this top up will expire", verbose_name="Expiration Date"
                    ),
                ),
                (
                    "stripe_charge",
                    models.CharField(
                        blank=True,
                        help_text="The Stripe charge id for this charge",
                        max_length=32,
                        null=True,
                        verbose_name="Stripe Charge Id",
                    ),
                ),
                (
                    "comment",
                    models.CharField(
                        blank=True,
                        help_text="Any comment associated with this topup, used when we credit accounts",
                        max_length=255,
                        null=True,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        help_text="The user which originally created this item",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orgs_topup_creations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        help_text="The user which last modified this item",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orgs_topup_modifications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "org",
                    models.ForeignKey(
                        help_text="The organization that was toppped up",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="topups",
                        to="orgs.Org",
                    ),
                ),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="TopUpCredits",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("used", models.IntegerField(help_text="How many credits were used, can be negative")),
                (
                    "topup",
                    models.ForeignKey(
                        help_text="The topup these credits are being used against",
                        on_delete=django.db.models.deletion.CASCADE,
                        to="orgs.TopUp",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="UserSettings",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "language",
                    models.CharField(
                        choices=[("en-us", "English"), ("pt-br", "Portuguese"), ("fr", "French"), ("es", "Spanish")],
                        default="en-us",
                        help_text="Your preferred language",
                        max_length=8,
                    ),
                ),
                (
                    "tel",
                    models.CharField(
                        blank=True,
                        help_text="Phone number for testing and recording voice flows",
                        max_length=16,
                        null=True,
                        verbose_name="Phone Number",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="settings",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="language",
            name="org",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="languages",
                to="orgs.Org",
                verbose_name="Org",
            ),
        ),
        migrations.AddField(
            model_name="invitation",
            name="org",
            field=models.ForeignKey(
                help_text="The organization to which the account is invited to view",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="invitations",
                to="orgs.Org",
                verbose_name="Org",
            ),
        ),
        migrations.AddField(
            model_name="debit",
            name="beneficiary",
            field=models.ForeignKey(
                help_text="Optional topup that was allocated with these credits",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="allocations",
                to="orgs.TopUp",
            ),
        ),
        migrations.AddField(
            model_name="debit",
            name="created_by",
            field=models.ForeignKey(
                help_text="The user which originally created this item",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="debits_created",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="debit",
            name="topup",
            field=models.ForeignKey(
                help_text="The topup these credits are applied against",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="debits",
                to="orgs.TopUp",
            ),
        ),
        migrations.AddField(
            model_name="creditalert",
            name="org",
            field=models.ForeignKey(
                help_text="The organization this alert was triggered for",
                on_delete=django.db.models.deletion.CASCADE,
                to="orgs.Org",
            ),
        ),
    ]
