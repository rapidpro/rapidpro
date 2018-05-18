from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0037_iso639-3_language_model'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='org',
            name='flows_last_viewed',
        ),
        migrations.RemoveField(
            model_name='org',
            name='msg_last_viewed',
        ),
    ]
