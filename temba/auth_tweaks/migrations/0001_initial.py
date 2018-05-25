from django.db import migrations


class Migration(migrations.Migration):

    dependencies = []

    operations = [migrations.RunSQL("ALTER TABLE auth_user ALTER COLUMN username TYPE VARCHAR(254);")]
