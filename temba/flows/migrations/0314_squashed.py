# This is a dummy migration which will be implemented in the next release

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("flows", "0313_squashed"),
        ("campaigns", "0051_squashed"),
        ("ivr", "0022_squashed"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("contacts", "0170_squashed"),
    ]

    operations = []
