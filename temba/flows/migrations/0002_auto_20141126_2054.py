# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('flows', '0001_initial'),
        ('orgs', '0001_initial'),
        ('msgs', '0001_initial'),
        ('contacts', '0001_initial'),
        ('ivr', '0002_auto_20141126_2054'),
    ]

    operations = [
        migrations.AddField(
            model_name='flowstep',
            name='messages',
            field=models.ManyToManyField(help_text='Any messages that are associated with this step (either sent or received)', related_name='steps', null=True, to='msgs.Msg'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='flowstep',
            name='run',
            field=models.ForeignKey(related_name='steps', to='flows.FlowRun'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='flowstart',
            name='contacts',
            field=models.ManyToManyField(help_text='Contacts that will start the flow', to='contacts.Contact', null=True, blank=True),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='flowstart',
            name='created_by',
            field=models.ForeignKey(related_name=b'flows_flowstart_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='flowstart',
            name='flow',
            field=models.ForeignKey(related_name='starts', to='flows.Flow', help_text='The flow that is being started'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='flowstart',
            name='groups',
            field=models.ManyToManyField(help_text='Groups that will start the flow', to='contacts.ContactGroup', null=True, blank=True),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='flowstart',
            name='modified_by',
            field=models.ForeignKey(related_name=b'flows_flowstart_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='flowrun',
            name='call',
            field=models.ForeignKey(related_name='runs', blank=True, to='ivr.IVRCall', help_text='The call that handled this flow run, only for voice flows', null=True),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='flowrun',
            name='contact',
            field=models.ForeignKey(related_name='runs', to='contacts.Contact'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='flowrun',
            name='flow',
            field=models.ForeignKey(related_name='runs', to='flows.Flow'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='flowrun',
            name='start',
            field=models.ForeignKey(related_name='runs', blank=True, to='flows.FlowStart', help_text='The FlowStart objects that started this run', null=True),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='flowlabel',
            name='org',
            field=models.ForeignKey(to='orgs.Org'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='flowlabel',
            name='parent',
            field=models.ForeignKey(related_name='children', verbose_name='Parent', to='flows.FlowLabel', null=True),
            preserve_default=True,
        ),
        migrations.AlterUniqueTogether(
            name='flowlabel',
            unique_together=set([('name', 'parent', 'org')]),
        ),
        migrations.AddField(
            model_name='flow',
            name='created_by',
            field=models.ForeignKey(related_name=b'flows_flow_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='flow',
            name='labels',
            field=models.ManyToManyField(help_text='Any labels on this flow', related_name='flows', verbose_name='Labels', to='flows.FlowLabel', blank=True),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='flow',
            name='modified_by',
            field=models.ForeignKey(related_name=b'flows_flow_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='flow',
            name='org',
            field=models.ForeignKey(related_name='flows', to='orgs.Org'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='flow',
            name='saved_by',
            field=models.ForeignKey(related_name='flow_saves', to=settings.AUTH_USER_MODEL, help_text='The user which last saved this flow'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='exportflowresultstask',
            name='created_by',
            field=models.ForeignKey(related_name=b'flows_exportflowresultstask_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='exportflowresultstask',
            name='flows',
            field=models.ManyToManyField(help_text='The flows to export', related_name='exports', to='flows.Flow'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='exportflowresultstask',
            name='modified_by',
            field=models.ForeignKey(related_name=b'flows_exportflowresultstask_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='actionset',
            name='destination',
            field=models.ForeignKey(related_name='sources', on_delete=django.db.models.deletion.SET_NULL, to='flows.RuleSet', help_text='The RuleSet that will interpret the response to this action (optional)', null=True),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='actionset',
            name='flow',
            field=models.ForeignKey(related_name='action_sets', to='flows.Flow'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='actionlog',
            name='run',
            field=models.ForeignKey(related_name='logs', to='flows.FlowRun'),
            preserve_default=True,
        ),
    ]
