# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('flows', '0001_initial'),
        ('schedules', '0001_initial'),
        ('channels', '0001_initial'),
        ('orgs', '0001_initial'),
        ('contacts', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Trigger',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('keyword', models.CharField(help_text='The first word in the message text', max_length=16, null=True, verbose_name='Keyword', blank=True)),
                ('last_triggered', models.DateTimeField(default=None, help_text='The last time this trigger was fired', null=True, verbose_name='Last Triggered')),
                ('trigger_count', models.IntegerField(default=0, help_text='How many times this trigger has fired', verbose_name='Trigger Count')),
                ('is_archived', models.BooleanField(default=False, help_text='Whether this trigger is archived', verbose_name='Is Archived')),
                ('trigger_type', models.CharField(default='K', help_text='The type of this trigger', max_length=1, verbose_name='Trigger Type', choices=[('K', 'Keyword Trigger'), ('S', 'Schedule Trigger'), ('V', 'Inbound Call Trigger'), ('M', 'Missed Call Trigger'), ('C', 'Catch All Trigger'), ('F', 'Follow Account Trigger')])),
                ('channel', models.OneToOneField(null=True, to='channels.Channel', help_text='The associated channel', verbose_name='Channel')),
                ('contacts', models.ManyToManyField(help_text='Individual contacts to broadcast the flow to', to='contacts.Contact', verbose_name='Contacts')),
                ('created_by', models.ForeignKey(related_name=b'triggers_trigger_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item')),
                ('flow', models.ForeignKey(related_name='triggers', blank=True, to='flows.Flow', help_text='Which flow will be started', null=True, verbose_name='Flow')),
                ('groups', models.ManyToManyField(help_text='The groups to broadcast the flow to', to='contacts.ContactGroup', verbose_name='Groups')),
                ('modified_by', models.ForeignKey(related_name=b'triggers_trigger_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item')),
                ('org', models.ForeignKey(verbose_name='Org', to='orgs.Org', help_text='The organization this trigger belongs to')),
                ('schedule', models.OneToOneField(related_name='trigger', null=True, to='schedules.Schedule', blank=True, help_text='Our recurring schedule', verbose_name='Schedule')),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
    ]
