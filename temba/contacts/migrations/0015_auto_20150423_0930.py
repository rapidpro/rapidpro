# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0014_populate_system_groups'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contactgroup',
            name='contacts',
            field=models.ManyToManyField(related_name='all_groups', verbose_name='Contacts', to='contacts.Contact'),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='contactgroup',
            name='org',
            field=models.ForeignKey(related_name='all_groups', verbose_name='Org', to='orgs.Org', help_text='The organization this group is part of'),
            preserve_default=True,
        ),
    ]
