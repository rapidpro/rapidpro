# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0042_ivrcall'),
    ]

    operations = [
        migrations.RenameField(
            model_name='channelsession',
            old_name='call_type',
            new_name='session_type'
        ),
        migrations.AlterField(
            model_name='channelsession',
            name='channel',
            field=models.ForeignKey(help_text='The channel that created this session', to='channels.Channel'),
        ),
        migrations.AlterField(
            model_name='channelsession',
            name='contact',
            field=models.ForeignKey(related_name='sessions', to='contacts.Contact', help_text='Who this session is with'),
        ),
        migrations.AlterField(
            model_name='channelsession',
            name='contact_urn',
            field=models.ForeignKey(verbose_name='Contact URN', to='contacts.ContactURN', help_text='The URN this session is communicating with'),
        ),
        migrations.AlterField(
            model_name='channelsession',
            name='created_by',
            field=models.ForeignKey(related_name='channels_channelsession_creations', to=settings.AUTH_USER_MODEL, help_text='The user which originally created this item'),
        ),
        migrations.AlterField(
            model_name='channelsession',
            name='direction',
            field=models.CharField(help_text='The direction of this session, either incoming or outgoing', max_length=1, choices=[('I', 'Incoming'), ('O', 'Outgoing')]),
        ),
        migrations.AlterField(
            model_name='channelsession',
            name='duration',
            field=models.IntegerField(default=0, help_text='The length of this session in seconds', null=True),
        ),
        migrations.AlterField(
            model_name='channelsession',
            name='ended_on',
            field=models.DateTimeField(help_text='When this session ended', null=True, blank=True),
        ),
        migrations.AlterField(
            model_name='channelsession',
            name='external_id',
            field=models.CharField(help_text='The external id for this session, our twilio id usually', max_length=255),
        ),
        migrations.AlterField(
            model_name='channelsession',
            name='flow',
            field=models.ForeignKey(to='flows.Flow', help_text='The flow this session was part of', null=True),
        ),
        migrations.AlterField(
            model_name='channelsession',
            name='modified_by',
            field=models.ForeignKey(related_name='channels_channelsession_modifications', to=settings.AUTH_USER_MODEL, help_text='The user which last modified this item'),
        ),
        migrations.AlterField(
            model_name='channelsession',
            name='org',
            field=models.ForeignKey(help_text='The organization this session belongs to', to='orgs.Org'),
        ),
        migrations.AlterField(
            model_name='channelsession',
            name='parent',
            field=models.ForeignKey(related_name='child_sessions', verbose_name='Parent Session', to='channels.ChannelSession', help_text='The session that triggered this one', null=True),
        ),
        migrations.AlterField(
            model_name='channelsession',
            name='started_on',
            field=models.DateTimeField(help_text='When this session was connected and started', null=True, blank=True),
        ),
        migrations.AlterField(
            model_name='channelsession',
            name='status',
            field=models.CharField(default='P', help_text='The status of this session', max_length=1, choices=[('Q', 'Queued'), ('R', 'Ringing'), ('I', 'In Progress'), ('D', 'Complete'), ('B', 'Busy'), ('F', 'Failed'), ('N', 'No Answer'), ('C', 'Canceled')]),
        ),
    ]
