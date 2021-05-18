# This is a dummy migration which will be implemented in 6.1

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("orgs", "0072_squashed"),
        ("schedules", "0014_squashed"),
        ("contacts", "0129_squashed"),
        ("channels", "0124_squashed"),
        ("msgs", "0143_broadcast_raw_urns"),
    ]

    operations = []
