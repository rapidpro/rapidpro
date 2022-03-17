# This is a dummy migration which will be implemented in 7.3

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("channels", "0137_squashed"),
        ("flows", "0278_squashed"),
        ("schedules", "0017_squashed"),
        ("tickets", "0027_squashed"),
        ("orgs", "0093_squashed"),
        ("contacts", "0153_squashed"),
        ("msgs", "0168_remove_msg_delete_from_counts_and_more"),
    ]

    operations = []
