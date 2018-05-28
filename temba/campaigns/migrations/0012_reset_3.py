import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("flows", "0079_reset_1"),
        ("contacts", "0046_reset_1"),
        ("campaigns", "0011_reset_2"),
    ]

    operations = [
        migrations.AddField(
            model_name="campaignevent",
            name="flow",
            field=models.ForeignKey(
                help_text="The flow that will be triggered",
                on_delete=django.db.models.deletion.CASCADE,
                to="flows.Flow",
            ),
        ),
        migrations.AddField(
            model_name="campaignevent",
            name="modified_by",
            field=models.ForeignKey(
                help_text="The user which last modified this item",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="campaigns_campaignevent_modifications",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="campaignevent",
            name="relative_to",
            field=models.ForeignKey(
                help_text="The field our offset is relative to",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="campaigns",
                to="contacts.ContactField",
            ),
        ),
        migrations.AddField(
            model_name="campaign",
            name="created_by",
            field=models.ForeignKey(
                help_text="The user which originally created this item",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="campaigns_campaign_creations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="campaign",
            name="group",
            field=models.ForeignKey(
                help_text="The group this campaign operates on",
                on_delete=django.db.models.deletion.CASCADE,
                to="contacts.ContactGroup",
            ),
        ),
        migrations.AddField(
            model_name="campaign",
            name="modified_by",
            field=models.ForeignKey(
                help_text="The user which last modified this item",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="campaigns_campaign_modifications",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
