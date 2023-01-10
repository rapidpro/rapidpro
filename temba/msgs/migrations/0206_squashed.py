# This is a dummy migration which will be implemented in the next release

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("channels", "0158_squashed"),
        ("flows", "0315_squashed"),
        ("schedules", "0018_squashed"),
        ("tickets", "0044_squashed"),
        ("orgs", "0118_squashed"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("contacts", "0171_squashed"),
        ("msgs", "0205_squashed"),
    ]

    operations = []
