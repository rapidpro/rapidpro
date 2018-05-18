from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0015_campaignevent_message_new'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='campaignevent',
            name='message',
        ),
        migrations.RenameField(
            model_name='campaignevent',
            old_name='message_new',
            new_name='message',
        ),
    ]
