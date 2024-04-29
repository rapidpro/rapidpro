# This is a dummy migration which will be implemented in the next release

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("orgs", "0119_squashed"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("api", "0040_squashed"),
    ]

    operations = []
