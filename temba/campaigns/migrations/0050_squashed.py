# This is a dummy migration which will be implemented in the next release

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("contacts", "0170_squashed"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("campaigns", "0049_squashed"),
    ]

    operations = []
