from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="AirtimeTransfer",
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
                        choices=[("P", "Pending"), ("S", "Success"), ("F", "Failed")],
                        default="P",
                        help_text="The state this event is currently in",
                        max_length=1,
                    ),
                ),
                ("recipient", models.CharField(max_length=64)),
                ("amount", models.FloatField()),
                ("denomination", models.CharField(blank=True, max_length=32, null=True)),
                ("data", models.TextField(blank=True, default="", null=True)),
                ("response", models.TextField(blank=True, default="", null=True)),
                (
                    "message",
                    models.CharField(
                        blank=True,
                        help_text="A message describing the end status, error messages go here",
                        max_length=255,
                        null=True,
                    ),
                ),
            ],
            options={"abstract": False},
        )
    ]
