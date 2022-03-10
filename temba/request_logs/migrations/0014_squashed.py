# This is a dummy migration which will be implemented in 7.3

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("orgs", "0093_squashed"),
        ("channels", "0137_squashed"),
        ("flows", "0278_squashed"),
        ("classifiers", "0007_squashed"),
        ("tickets", "0027_squashed"),
        ("airtime", "0021_squashed"),
        ("request_logs", "0013_auto_20210928_1505"),
    ]

    operations = []
