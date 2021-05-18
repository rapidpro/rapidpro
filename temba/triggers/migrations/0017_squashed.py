# This is a dummy migration which will be implemented in 6.1

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("orgs", "0072_squashed"),
        ("schedules", "0014_squashed"),
        ("contacts", "0128_squashed"),
        ("channels", "0124_squashed"),
        ("flows", "0243_squashed"),
        ("triggers", "0016_auto_20190816_1517"),
    ]

    operations = []
