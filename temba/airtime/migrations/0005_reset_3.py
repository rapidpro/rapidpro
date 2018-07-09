import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("contacts", "0046_reset_1"),
        ("airtime", "0004_reset_2"),
    ]

    operations = [
        migrations.AddField(
            model_name="airtimetransfer",
            name="contact",
            field=models.ForeignKey(
                help_text="The contact that this airtime is sent to",
                on_delete=django.db.models.deletion.CASCADE,
                to="contacts.Contact",
            ),
        ),
        migrations.AddField(
            model_name="airtimetransfer",
            name="created_by",
            field=models.ForeignKey(
                help_text="The user which originally created this item",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="airtime_airtimetransfer_creations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="airtimetransfer",
            name="modified_by",
            field=models.ForeignKey(
                help_text="The user which last modified this item",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="airtime_airtimetransfer_modifications",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
