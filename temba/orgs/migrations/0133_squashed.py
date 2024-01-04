# This is a dummy migration which will be implemented in the next release

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("orgs", "0132_alter_org_input_collation"),
    ]

    operations = []
