# This is a dummy migration which will be implemented in 7.3

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0036_squashed"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("orgs", "0093_squashed"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = []
