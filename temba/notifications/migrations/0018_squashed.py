# This is a dummy migration which will be implemented in the next release

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("msgs", "0254_squashed"),
        ("notifications", "0017_squashed"),
        ("tickets", "0057_squashed"),
        ("orgs", "0133_squashed"),
        ("contacts", "0184_squashed"),
        ("channels", "0182_squashed"),
        ("flows", "0330_squashed"),
    ]

    operations = []
