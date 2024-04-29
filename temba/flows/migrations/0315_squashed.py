# This is a dummy migration which will be implemented in the next release

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("templates", "0013_squashed"),
        ("classifiers", "0011_squashed"),
        ("channels", "0158_squashed"),
        ("globals", "0009_squashed"),
        ("ivr", "0022_squashed"),
        ("flows", "0314_squashed"),
        ("tickets", "0044_squashed"),
        ("orgs", "0118_squashed"),
        ("contacts", "0171_squashed"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("msgs", "0205_squashed"),
    ]

    operations = []
