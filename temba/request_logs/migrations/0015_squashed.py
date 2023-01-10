# This is a dummy migration which will be implemented in the next release

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("classifiers", "0011_squashed"),
        ("channels", "0158_squashed"),
        ("flows", "0315_squashed"),
        ("tickets", "0044_squashed"),
        ("airtime", "0024_squashed"),
        ("orgs", "0119_squashed"),
        ("request_logs", "0014_squashed"),
    ]

    operations = []
