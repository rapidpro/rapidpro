# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0036_reevaluate_dynamic_groups'),
    ]

    operations = [
        migrations.RenameField(
            model_name='contact',
            old_name='is_failed',
            new_name='is_stopped'
        ),
        migrations.AlterField(
            model_name='contact',
            name='is_stopped',
            field=models.BooleanField(default=False, help_text='Whether this contact has opted out of receiving messages',
                                      verbose_name='Is Stopped'),
        ),
        migrations.AlterField(
            model_name='contactgroup',
            name='group_type',
            field=models.CharField(default='U', help_text='What type of group it is, either user defined or one of our system groups',
                                   max_length=1, choices=[('A', 'All Contacts'), ('B', 'Blocked Contacts'), ('S', 'Stopped Contacts'),
                                                          ('U', 'User Defined Groups')]),
        ),
    ]
