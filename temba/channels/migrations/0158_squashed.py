# This is a dummy migration which will be implemented in the next release

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("orgs", "0118_squashed"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("channels", "0157_squashed"),
    ]

    operations = []
