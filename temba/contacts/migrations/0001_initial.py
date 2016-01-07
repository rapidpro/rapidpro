# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.conf import settings
import temba.orgs.models

class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Contact',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('name', models.CharField(help_text='The name of this contact', max_length=128, null=True, verbose_name='Name', blank=True)),
                ('uuid', models.CharField(help_text='The unique identifier for this contact.', unique=True, max_length=36, verbose_name='Unique Identifier')),
                ('is_archived', models.BooleanField(default=False, help_text='Whether this contacts has been archived', verbose_name='Is Archived')),
                ('is_test', models.BooleanField(default=False, help_text='Whether this contact is for simulation', verbose_name='Is Test')),
                ('status', models.CharField(default='N', max_length=2, verbose_name='Contact Status')),
                ('fields', models.CharField(null=True, max_length=128)),
                ('language', models.CharField(help_text='The preferred language for this contact', max_length=3, null=True, verbose_name='Language', blank=True)),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='ContactField',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('label', models.CharField(max_length=36, verbose_name='Label')),
                ('key', models.CharField(max_length=36, verbose_name='Key')),
                ('is_active', models.BooleanField(default=True, verbose_name='Is Active')),
                ('value_type', models.CharField(default=b'T', max_length=1, verbose_name='Field Type', choices=[(b'T', b'Text'), (b'N', b'Numeric'), (b'D', b'Date & Time'), (b'S', b'State'), (b'I', b'District')])),
                ('show_in_table', models.BooleanField(default=False, verbose_name='Shown in Tables')),
            ],
            options={
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='ContactGroup',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('name', models.CharField(help_text='The name for this contact group', max_length=64, verbose_name='Name')),
                ('query', models.TextField(help_text='The membership query for this group', null=True)),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='ContactURN',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('urn', models.CharField(help_text='The Universal Resource Name as a string. ex: tel:+250788383383', max_length=255, choices=[('tel', 'Phone number'), ('twitter', 'Twitter handle')])),
                ('path', models.CharField(help_text='The path component of our URN. ex: +250788383383', max_length=255)),
                ('scheme', models.CharField(help_text='The scheme for this URN, broken out for optimization reasons, ex: tel', max_length=128)),
                ('priority', models.IntegerField(default=50, help_text='The priority of this URN for the contact it is associated with')),
            ],
            options={
                'ordering': ('-priority', 'id'),
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='ExportContactsTask',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('host', models.CharField(help_text='The host this export task was created on', max_length=32)),
                ('filename', models.CharField(help_text='The file name for our export', max_length=64, null=True)),
                ('task_id', models.CharField(max_length=64, null=True)),
                ('created_by', models.ForeignKey(related_name=b'contacts_exportcontactstask_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item')),
                ('group', models.ForeignKey(related_name='exports', to='contacts.ContactGroup', help_text='The unique group to export', null=True)),
                ('modified_by', models.ForeignKey(related_name=b'contacts_exportcontactstask_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item')),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
    ]
