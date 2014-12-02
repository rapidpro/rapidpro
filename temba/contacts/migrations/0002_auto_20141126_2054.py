# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0001_initial'),
        ('csv_imports', '__first__'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('contacts', '0001_initial'),
        ('channels', '0002_auto_20141126_2054'),
    ]

    operations = [
        migrations.AddField(
            model_name='exportcontactstask',
            name='org',
            field=models.ForeignKey(related_name='contacts_exports', to='orgs.Org', help_text='The Organization of the user.'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='contacturn',
            name='channel',
            field=models.ForeignKey(blank=True, to='channels.Channel', help_text='The preferred channel for this URN', null=True),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='contacturn',
            name='contact',
            field=models.ForeignKey(related_name='urns', blank=True, to='contacts.Contact', help_text='The contact that this URN is for, can be null', null=True),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='contacturn',
            name='org',
            field=models.ForeignKey(help_text='The organization for this URN, can be null', to='orgs.Org'),
            preserve_default=True,
        ),
        migrations.AlterUniqueTogether(
            name='contacturn',
            unique_together=set([('urn', 'org')]),
        ),
        migrations.AddField(
            model_name='contactgroup',
            name='contacts',
            field=models.ManyToManyField(related_name='groups', verbose_name='Contacts', to='contacts.Contact'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='contactgroup',
            name='created_by',
            field=models.ForeignKey(related_name=b'contacts_contactgroup_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='contactgroup',
            name='import_task',
            field=models.ForeignKey(blank=True, to='csv_imports.ImportTask', null=True),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='contactgroup',
            name='modified_by',
            field=models.ForeignKey(related_name=b'contacts_contactgroup_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='contactgroup',
            name='org',
            field=models.ForeignKey(verbose_name='Org', to='orgs.Org', help_text='The organization this group is part of'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='contactgroup',
            name='query_fields',
            field=models.ManyToManyField(to='contacts.ContactField', verbose_name='Query Fields'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='contactfield',
            name='org',
            field=models.ForeignKey(related_name='contactfields', verbose_name='Org', to='orgs.Org'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='contact',
            name='created_by',
            field=models.ForeignKey(related_name=b'contacts_contact_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='contact',
            name='modified_by',
            field=models.ForeignKey(related_name=b'contacts_contact_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='contact',
            name='org',
            field=models.ForeignKey(related_name='org_contacts', verbose_name='Org', to='orgs.Org', help_text='The organization that this contact belongs to'),
            preserve_default=True,
        ),
    ]
