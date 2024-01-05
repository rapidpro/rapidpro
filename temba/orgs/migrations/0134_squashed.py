# This is a dummy migration which will be implemented in the next release

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("locations", "0030_squashed"),
        ("tickets", "0057_squashed"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("orgs", "0133_squashed"),
    ]

    operations = []
