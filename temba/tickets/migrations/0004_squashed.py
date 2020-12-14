# This is a dummy migration which will be implemented in 6.1

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("orgs", "0072_squashed"),
        ("contacts", "0128_squashed"),
        ("tickets", "0003_auto_20200602_1522"),
    ]

    operations = []
