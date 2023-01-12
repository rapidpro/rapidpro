# This is a dummy migration which will be implemented in the next release

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("orgs", "0118_squashed"),
        ("flows", "0314_squashed"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("contacts", "0170_squashed"),
        ("tickets", "0043_exportticketstask_with_groups"),
    ]

    operations = []
