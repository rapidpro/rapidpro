# This is a dummy migration which will be implemented in 6.1

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("classifiers", "0004_squashed"),
        ("orgs", "0072_squashed"),
        ("channels", "0125_squashed"),
        ("contacts", "0129_squashed"),
        ("globals", "0003_squashed"),
        ("msgs", "0144_squashed"),
        ("flows", "0243_squashed"),
        ("tickets", "0004_squashed"),
        ("templates", "0007_squashed"),
    ]

    operations = []
