# This is a dummy migration which will be implemented in 7.3

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("orgs", "0093_squashed"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("schedules", "0016_alter_schedule_created_by_alter_schedule_modified_by"),
    ]

    operations = []
