# This is a dummy migration which will be implemented in the next release

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("locations", "0025_squashed"),
    ]

    operations = []
