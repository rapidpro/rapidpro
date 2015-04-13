# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations, connection

class Migration(migrations.Migration):

    def set_group_type(apps, schema_editor):
        ContactGroup = apps.get_model('contacts', 'ContactGroup')
        ContactGroup.objects.filter(group_type=None).update(group_type='U')

    dependencies = [
        ('contacts', '0012_install_group_triggers'),
    ]

    operations = [
        migrations.AddField(
            model_name='contactgroup',
            name='group_type',
            field=models.CharField(null=True, help_text='What type of group it is, either user defined or one of our system groups', max_length=1, choices=[('A', 'All Contacts'), ('B', 'Blocked Contacts'), ('F', 'Failed Contacts'), ('U', 'User Defined Groups')]),
        ),
        migrations.RunPython(
            set_group_type,
        ),
        migrations.AlterField(
            model_name='contactgroup',
            name='group_type',
            field=models.CharField(default='U', help_text='What type of group it is, either user defined or one of our system groups', max_length=1, choices=[('A', 'All Contacts'), ('B', 'Blocked Contacts'), ('F', 'Failed Contacts'), ('U', 'User Defined Groups')]),
        ),

    ]
