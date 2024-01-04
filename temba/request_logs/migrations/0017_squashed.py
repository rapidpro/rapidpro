# This is a dummy migration which will be implemented in the next release

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("orgs", "0133_squashed"),
        ("flows", "0329_squashed"),
        ("classifiers", "0012_squashed"),
        ("airtime", "0026_squashed"),
        ("channels", "0181_squashed"),
        ("request_logs", "0016_remove_httplog_request_log_tickete_abc69b_idx_and_more"),
    ]

    operations = []
