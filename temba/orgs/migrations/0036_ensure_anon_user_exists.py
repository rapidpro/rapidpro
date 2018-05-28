from django.conf import settings
from django.contrib.auth.models import User
from django.db import migrations


def ensure_anon_user_exists(apps, schema_editor):
    if not User.objects.filter(username=settings.ANONYMOUS_USER_NAME).exists():
        user = User(username=settings.ANONYMOUS_USER_NAME)
        user.set_unusable_password()
        user.save()


class Migration(migrations.Migration):

    dependencies = [("orgs", "0035_auto_20170614_0915"), ("auth", "0008_alter_user_username_max_length")]

    operations = [migrations.RunPython(ensure_anon_user_exists)]
