import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="Schedule",
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
                    models.CharField(choices=[("U", "Unscheduled"), ("S", "Scheduled")], default="U", max_length=1),
                ),
                ("repeat_hour_of_day", models.IntegerField(help_text="The hour of the day", null=True)),
                ("repeat_minute_of_hour", models.IntegerField(help_text="The minute of the hour", null=True)),
                ("repeat_day_of_month", models.IntegerField(help_text="The day of the month to repeat on", null=True)),
                (
                    "repeat_period",
                    models.CharField(
                        choices=[("O", "Never"), ("D", "Daily"), ("W", "Weekly"), ("M", "Monthly")],
                        help_text="When this schedule repeats",
                        max_length=1,
                        null=True,
                    ),
                ),
                (
                    "repeat_days",
                    models.IntegerField(blank=True, default=0, help_text="bit mask of days of the week", null=True),
                ),
                (
                    "last_fire",
                    models.DateTimeField(
                        blank=True, default=None, help_text="When this schedule last fired", null=True
                    ),
                ),
                (
                    "next_fire",
                    models.DateTimeField(
                        blank=True, default=None, help_text="When this schedule fires next", null=True
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        help_text="The user which originally created this item",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="schedules_schedule_creations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        help_text="The user which last modified this item",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="schedules_schedule_modifications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"abstract": False},
        )
    ]
