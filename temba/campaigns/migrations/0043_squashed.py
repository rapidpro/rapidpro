# This is a dummy migration which will be implemented in 7.3

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("campaigns", "0042_squashed"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("contacts", "0152_squashed"),
    ]

    operations = []
