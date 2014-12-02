# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
import django.utils.timezone
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('contacts', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ActionLog',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('text', models.TextField(help_text='The log text')),
                ('created_on', models.DateTimeField(help_text='When this action log was created', auto_now_add=True)),
            ],
            options={
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='ActionSet',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('uuid', models.CharField(unique=True, max_length=36)),
                ('actions', models.TextField(help_text='The JSON encoded actions for this action set')),
                ('x', models.IntegerField()),
                ('y', models.IntegerField()),
                ('created_on', models.DateTimeField(help_text='When this action was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text='When this action was last modified', auto_now=True)),
            ],
            options={
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='ExportFlowResultsTask',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('host', models.CharField(help_text='The host this export task was created on', max_length=32)),
                ('filename', models.CharField(help_text='The file name for our export', max_length=64, null=True)),
                ('task_id', models.CharField(max_length=64, null=True)),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='Flow',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('name', models.CharField(help_text='The name for this flow', max_length=64)),
                ('entry_uuid', models.CharField(max_length=36, unique=True, null=True)),
                ('entry_type', models.CharField(help_text='The type of node this flow starts with', max_length=1, null=True, choices=[('R', 'Rules'), ('A', 'Actions')])),
                ('is_archived', models.BooleanField(default=False, help_text='Whether this flow is archived')),
                ('flow_type', models.CharField(default='F', help_text='The type of this flow', max_length=1, choices=[('F', 'Message flow'), ('M', 'Single Message Flow'), ('V', 'Phone call flow')])),
                ('metadata', models.TextField(help_text='Any extra metadata attached to this flow, strictly used by the user interface.', null=True, blank=True)),
                ('expires_after_minutes', models.IntegerField(default=720, help_text='Minutes of inactivity that will cause expiration from flow')),
                ('ignore_triggers', models.BooleanField(default=False, help_text='Ignore keyword triggers while in this flow')),
                ('saved_on', models.DateTimeField(help_text='When this item was saved', auto_now_add=True)),
                ('base_language', models.CharField(help_text='The primary language for editing this flow', max_length=3, null=True, blank=True)),
            ],
            options={
                'ordering': ('-modified_on',),
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='FlowLabel',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('name', models.CharField(help_text='The name of this flow label', max_length=64, verbose_name='Name')),
            ],
            options={
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='FlowRun',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text='Whether this flow run is currently active')),
                ('fields', models.TextField(help_text='A JSON representation of any custom flow values the user has saved away', null=True, blank=True)),
                ('created_on', models.DateTimeField(default=django.utils.timezone.now, help_text='When this flow run was created')),
                ('expires_on', models.DateTimeField(help_text='When this flow run will expire', null=True, blank=True)),
                ('expired_on', models.DateTimeField(help_text='When this flow run expired', null=True, blank=True)),
            ],
            options={
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='FlowStart',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('restart_participants', models.BooleanField(default=True, help_text='Whether to restart any participants already in this flow')),
                ('contact_count', models.IntegerField(default=0, help_text='How many unique contacts were started down the flow')),
                ('status', models.CharField(default='P', help_text='The status of this flow start', max_length=1, choices=[('P', 'Pending'), ('S', 'Starting'), ('C', 'Complete'), ('F', 'Failed')])),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='FlowStep',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('step_type', models.CharField(help_text='What type of node was visited', max_length=1, choices=[('R', 'RuleSet'), ('A', 'ActionSet')])),
                ('step_uuid', models.CharField(help_text='The UUID of the ActionSet or RuleSet for this step', max_length=36, db_index=True)),
                ('rule_uuid', models.CharField(help_text='For uuid of the rule that matched on this ruleset, null on ActionSets', max_length=36, null=True)),
                ('rule_category', models.CharField(help_text='The category label that matched on this ruleset, null on ActionSets', max_length=36, null=True)),
                ('rule_value', models.CharField(help_text='The value that was matched in our category for this ruleset, null on ActionSets', max_length=640, null=True)),
                ('rule_decimal_value', models.DecimalField(help_text='The decimal value that was matched in our category for this ruleset, null on ActionSets or if a non numeric rule was matched', null=True, max_digits=36, decimal_places=8)),
                ('next_uuid', models.CharField(help_text='The uuid of the next step type we took', max_length=36, null=True)),
                ('arrived_on', models.DateTimeField(help_text='When the user arrived at this step in the flow', auto_now_add=True)),
                ('left_on', models.DateTimeField(help_text='When the user left this step in the flow', null=True, db_index=True)),
                ('contact', models.ForeignKey(related_name='flow_steps', to='contacts.Contact')),
            ],
            options={
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='FlowVersion',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('definition', models.TextField(help_text='The JSON flow definition')),
                ('created_by', models.ForeignKey(related_name=b'flows_flowversion_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item')),
                ('flow', models.ForeignKey(related_name='versions', to='flows.Flow')),
                ('modified_by', models.ForeignKey(related_name=b'flows_flowversion_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item')),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='RuleSet',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('uuid', models.CharField(unique=True, max_length=36)),
                ('label', models.CharField(help_text='The label for this field', max_length=64, null=True, blank=True)),
                ('operand', models.CharField(help_text='The value that rules will be run against, if None defaults to @step.value', max_length=128, null=True, blank=True)),
                ('webhook_url', models.URLField(help_text="The URL that will be called with the user's response before we run our rules", max_length=255, null=True, blank=True)),
                ('webhook_action', models.CharField(default='POST', max_length=8, null=True, help_text='How the webhook should be executed', blank=True)),
                ('rules', models.TextField(help_text='The JSON encoded actions for this action set')),
                ('finished_key', models.CharField(help_text='During IVR, this is the key to indicate we are done waiting', max_length=1, null=True, blank=True)),
                ('value_type', models.CharField(default=b'T', help_text='The type of value this ruleset saves', max_length=1, choices=[(b'T', b'Text'), (b'N', b'Numeric'), (b'D', b'Date & Time'), (b'S', b'State'), (b'I', b'District')])),
                ('response_type', models.CharField(default='O', help_text='The type of response that is being saved', max_length=1, choices=[('O', 'Open Ended'), ('C', 'Multiple Choice'), ('N', 'Numeric'), ('M', 'Menu'), ('K', 'Keypad'), ('R', 'Recording')])),
                ('x', models.IntegerField()),
                ('y', models.IntegerField()),
                ('created_on', models.DateTimeField(help_text='When this ruleset was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text='When this ruleset was last modified', auto_now=True)),
                ('flow', models.ForeignKey(related_name='rule_sets', to='flows.Flow')),
            ],
            options={
            },
            bases=(models.Model,),
        ),
    ]
