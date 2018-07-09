import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("contacts", "0046_reset_1"),
        ("campaigns", "0010_reset_1"),
    ]

    operations = [
        migrations.AddField(
            model_name="eventfire",
            name="contact",
            field=models.ForeignKey(
                help_text="The contact that is scheduled to have an event run",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="fire_events",
                to="contacts.Contact",
            ),
        ),
        migrations.AddField(
            model_name="eventfire",
            name="event",
            field=models.ForeignKey(
                help_text="The event that will be fired",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="event_fires",
                to="campaigns.CampaignEvent",
            ),
        ),
        migrations.AddField(
            model_name="campaignevent",
            name="campaign",
            field=models.ForeignKey(
                help_text="The campaign this event is part of",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="events",
                to="campaigns.Campaign",
            ),
        ),
        migrations.AddField(
            model_name="campaignevent",
            name="created_by",
            field=models.ForeignKey(
                help_text="The user which originally created this item",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="campaigns_campaignevent_creations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
