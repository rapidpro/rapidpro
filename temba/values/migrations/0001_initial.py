# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0001_initial'),
        ('flows', '0001_initial'),
        ('contacts', '0001_initial'),
        ('orgs', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Value',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('rule_uuid', models.CharField(help_text=b'The rule that matched, only appropriate for RuleSet values', max_length=255, null=True)),
                ('category', models.CharField(help_text=b'The name of the category this value matched in the RuleSet', max_length=36, null=True)),
                ('string_value', models.TextField(help_text=b'The string value or string representation of this value', max_length=640)),
                ('decimal_value', models.DecimalField(help_text=b'The decimal value of this value if any.', null=True, max_digits=36, decimal_places=8)),
                ('datetime_value', models.DateTimeField(help_text=b'The datetime value of this value if any.', null=True)),
                ('recording_value', models.TextField(help_text=b'The recording url if any.', max_length=640, null=True)),
                ('created_on', models.DateTimeField(auto_now_add=True)),
                ('modified_on', models.DateTimeField(auto_now=True)),
                ('contact', models.ForeignKey(related_name=b'values', to='contacts.Contact')),
                ('contact_field', models.ForeignKey(on_delete=django.db.models.deletion.SET_NULL, to='contacts.ContactField', help_text=b'The ContactField this value is for, if any', null=True)),
                ('location_value', models.ForeignKey(on_delete=django.db.models.deletion.SET_NULL, to='locations.AdminBoundary', help_text=b'The location value of this value if any.', null=True)),
                ('org', models.ForeignKey(to='orgs.Org')),
                ('ruleset', models.ForeignKey(on_delete=django.db.models.deletion.SET_NULL, to='flows.RuleSet', help_text=b'The RuleSet this value is for, if any', null=True)),
                ('run', models.ForeignKey(related_name=b'values', on_delete=django.db.models.deletion.SET_NULL, to='flows.FlowRun', help_text=b'The FlowRun this value is for, if any', null=True)),
            ],
            options={
            },
            bases=(models.Model,),
        ),
    ]
