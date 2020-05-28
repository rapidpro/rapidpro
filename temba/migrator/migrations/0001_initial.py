# Generated by Django 2.2.4 on 2020-05-28 19:00

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import temba.utils.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('orgs', '0060_merge_20200510_1733'),
    ]

    operations = [
        migrations.CreateModel(
            name='MigrationTask',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_active', models.BooleanField(default=True, help_text='Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(blank=True, default=django.utils.timezone.now, editable=False, help_text='When this item was originally created')),
                ('modified_on', models.DateTimeField(blank=True, default=django.utils.timezone.now, editable=False, help_text='When this item was last modified')),
                ('uuid', models.CharField(db_index=True, default=temba.utils.models.generate_uuid, help_text='The unique identifier for this object', max_length=36, unique=True, verbose_name='Unique Identifier')),
                ('status', models.CharField(choices=[('P', 'Pending'), ('O', 'Processing'), ('C', 'Complete'), ('F', 'Failed')], default='P', max_length=1)),
                ('created_by', models.ForeignKey(help_text='The user which originally created this item', on_delete=django.db.models.deletion.PROTECT, related_name='migrator_migrationtask_creations', to=settings.AUTH_USER_MODEL)),
                ('modified_by', models.ForeignKey(help_text='The user which last modified this item', on_delete=django.db.models.deletion.PROTECT, related_name='migrator_migrationtask_modifications', to=settings.AUTH_USER_MODEL)),
                ('org', models.ForeignKey(help_text='The organization of this import progress.', on_delete=django.db.models.deletion.PROTECT, related_name='migrationtasks', to='orgs.Org')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
