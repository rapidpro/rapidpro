# This is a dummy migration which will be implemented in the next release

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("archives", "0019_squashed"),
        ("orgs", "0133_squashed"),
    ]

    operations = []
