# This is a dummy migration which will be implemented in the next release

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("ivr", "0028_squashed"),
        ("tickets", "0057_squashed"),
        ("globals", "0011_squashed"),
        ("orgs", "0133_squashed"),
        ("flows", "0329_squashed"),
        ("contacts", "0184_squashed"),
        ("channels", "0182_squashed"),
        ("classifiers", "0013_squashed"),
        ("templates", "0020_squashed"),
        ("msgs", "0253_squashed"),
    ]

    operations = []
