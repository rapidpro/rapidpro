import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="APIToken",
            fields=[
                ("is_active", models.BooleanField(default=True)),
                ("key", models.CharField(max_length=40, primary_key=True, serialize=False)),
                ("created", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name="Resthook",
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
                ("slug", models.SlugField(help_text="A simple label for this event")),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="ResthookSubscriber",
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
                ("target_url", models.URLField(help_text="The URL that we will call when our ruleset is reached")),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="WebHookEvent",
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
                    "status",
                    models.CharField(
                        choices=[("P", "Pending"), ("C", "Complete"), ("E", "Errored"), ("F", "Failed")],
                        default="P",
                        help_text="The state this event is currently in",
                        max_length=1,
                    ),
                ),
                (
                    "event",
                    models.CharField(
                        choices=[
                            ("mo_sms", "Incoming SMS Message"),
                            ("mt_sent", "Outgoing SMS Sent"),
                            ("mt_dlvd", "Outgoing SMS Delivered to Recipient"),
                            ("mt_call", "Outgoing Call"),
                            ("mt_miss", "Missed Outgoing Call"),
                            ("mo_call", "Incoming Call"),
                            ("mo_miss", "Missed Incoming Call"),
                            ("alarm", "Channel Alarm"),
                            ("flow", "Flow Step Reached"),
                            ("categorize", "Flow Categorization"),
                        ],
                        help_text="The event type for this event",
                        max_length=16,
                    ),
                ),
                ("data", models.TextField(help_text="The JSON encoded data that will be POSTED to the web hook")),
                (
                    "try_count",
                    models.IntegerField(default=0, help_text="The number of times this event has been tried"),
                ),
                (
                    "next_attempt",
                    models.DateTimeField(blank=True, help_text="When this event will be retried", null=True),
                ),
                ("action", models.CharField(default="POST", help_text="What type of HTTP event is it", max_length=8)),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="WebHookResult",
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
                ("url", models.TextField(blank=True, help_text="The URL the event was delivered to", null=True)),
                ("data", models.TextField(blank=True, help_text="The data that was posted to the webhook", null=True)),
                (
                    "request",
                    models.TextField(blank=True, help_text="The request that was posted to the webhook", null=True),
                ),
                ("status_code", models.IntegerField(help_text="The HTTP status as returned by the web hook")),
                (
                    "message",
                    models.CharField(
                        help_text="A message describing the result, error messages go here", max_length=255
                    ),
                ),
                (
                    "body",
                    models.TextField(
                        blank=True, help_text="The body of the HTTP response as returned by the web hook", null=True
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        help_text="The user which originally created this item",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="api_webhookresult_creations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "event",
                    models.ForeignKey(
                        help_text="The event that this result is tied to",
                        on_delete=django.db.models.deletion.CASCADE,
                        to="api.WebHookEvent",
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        help_text="The user which last modified this item",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="api_webhookresult_modifications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"abstract": False},
        ),
    ]
