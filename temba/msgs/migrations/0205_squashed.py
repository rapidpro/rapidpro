# This is a dummy migration which will be implemented in 7.3

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("msgs", "0204_remove_msg_msgs_next_attempt_out_errored_and_more"),
    ]

    operations = []
