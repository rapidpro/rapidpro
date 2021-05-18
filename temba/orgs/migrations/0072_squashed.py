# This is a dummy migration which will be implemented in 6.1

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("locations", "0020_squashed"),
        ("orgs", "0071_orgactivity_plan_active_contact_count"),
    ]

    operations = []
