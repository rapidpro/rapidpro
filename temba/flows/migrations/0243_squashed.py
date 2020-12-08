# This is a dummy migration which will be implemented in 6.1

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("channels", "0124_squashed"),
        ("campaigns", "0036_squashed"),
        ("contacts", "0128_squashed"),
        ("flows", "0242_drop_run_timeout"),
    ]

    operations = []
