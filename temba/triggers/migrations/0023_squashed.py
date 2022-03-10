# This is a dummy migration which will be implemented in 7.3

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("channels", "0137_squashed"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("flows", "0278_squashed"),
        ("schedules", "0017_squashed"),
        ("contacts", "0152_squashed"),
        ("orgs", "0093_squashed"),
        ("triggers", "0022_alter_trigger_created_by_alter_trigger_modified_by"),
    ]

    operations = []
