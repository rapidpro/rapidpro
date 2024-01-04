# This is a dummy migration which will be implemented in the next release

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("contacts", "0183_squashed"),
        ("orgs", "0133_squashed"),
        ("flows", "0329_squashed"),
        ("schedules", "0027_squashed"),
        ("channels", "0181_squashed"),
        (
            "triggers",
            "0039_remove_trigger_triggers_scheduled_trigger_has_schedule_and_more",
        ),
    ]

    operations = []
