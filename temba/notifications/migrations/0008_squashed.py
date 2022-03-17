# This is a dummy migration which will be implemented in 7.3

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("msgs", "0169_squashed"),
        ("channels", "0137_squashed"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("flows", "0278_squashed"),
        ("orgs", "0093_squashed"),
        ("contacts", "0153_squashed"),
        ("notifications", "0007_remove_alert_notifications"),
    ]

    operations = []
