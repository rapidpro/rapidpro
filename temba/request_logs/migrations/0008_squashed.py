# This is a dummy migration which will be implemented in 6.1

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("classifiers", "0004_squashed"),
        ("orgs", "0072_squashed"),
        ("channels", "0124_squashed"),
        ("airtime", "0017_squashed"),
        ("tickets", "0004_squashed"),
        ("request_logs", "0007_auto_20200526_1931"),
    ]

    operations = []
