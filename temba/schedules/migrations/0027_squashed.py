# This is a dummy migration which will be implemented in the next release

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("orgs", "0133_squashed"),
        ("schedules", "0026_delete_for_inactive_triggers"),
    ]

    operations = []
