import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("api", "0009_reset_1"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("channels", "0050_reset_1"),
    ]

    operations = [
        migrations.AddField(
            model_name="webhookevent",
            name="channel",
            field=models.ForeignKey(
                blank=True,
                help_text="The channel that this event is relating to",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to="channels.Channel",
            ),
        ),
        migrations.AddField(
            model_name="webhookevent",
            name="created_by",
            field=models.ForeignKey(
                help_text="The user which originally created this item",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="api_webhookevent_creations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="webhookevent",
            name="modified_by",
            field=models.ForeignKey(
                help_text="The user which last modified this item",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="api_webhookevent_modifications",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
