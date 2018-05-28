import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("api", "0010_reset_2"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("orgs", "0029_reset_1"),
    ]

    operations = [
        migrations.AddField(
            model_name="webhookevent",
            name="org",
            field=models.ForeignKey(
                help_text="The organization that this event was triggered for",
                on_delete=django.db.models.deletion.CASCADE,
                to="orgs.Org",
            ),
        ),
        migrations.AddField(
            model_name="webhookevent",
            name="resthook",
            field=models.ForeignKey(
                help_text="The associated resthook to this event. (optional)",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to="api.Resthook",
            ),
        ),
        migrations.AddField(
            model_name="resthooksubscriber",
            name="created_by",
            field=models.ForeignKey(
                help_text="The user which originally created this item",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="api_resthooksubscriber_creations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="resthooksubscriber",
            name="modified_by",
            field=models.ForeignKey(
                help_text="The user which last modified this item",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="api_resthooksubscriber_modifications",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="resthooksubscriber",
            name="resthook",
            field=models.ForeignKey(
                help_text="The resthook being subscribed to",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="subscribers",
                to="api.Resthook",
            ),
        ),
        migrations.AddField(
            model_name="resthook",
            name="created_by",
            field=models.ForeignKey(
                help_text="The user which originally created this item",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="api_resthook_creations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="resthook",
            name="modified_by",
            field=models.ForeignKey(
                help_text="The user which last modified this item",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="api_resthook_modifications",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="resthook",
            name="org",
            field=models.ForeignKey(
                help_text="The organization this resthook belongs to",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="resthooks",
                to="orgs.Org",
            ),
        ),
        migrations.AddField(
            model_name="apitoken",
            name="org",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name="api_tokens", to="orgs.Org"
            ),
        ),
        migrations.AddField(
            model_name="apitoken",
            name="role",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="auth.Group"),
        ),
        migrations.AddField(
            model_name="apitoken",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name="api_tokens", to=settings.AUTH_USER_MODEL
            ),
        ),
    ]
