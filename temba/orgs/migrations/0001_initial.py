# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunSQL('CREATE EXTENSION IF NOT EXISTS hstore'),
        migrations.CreateModel(
            name='CreditAlert',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('threshold', models.IntegerField(help_text='The threshold this alert was sent for')),
                ('created_by', models.ForeignKey(related_name=b'orgs_creditalert_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item')),
                ('modified_by', models.ForeignKey(related_name=b'orgs_creditalert_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item')),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='Invitation',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('email', models.EmailField(help_text='The email to which we send the invitation of the viewer', max_length=75, verbose_name='Email')),
                ('secret', models.CharField(help_text='a unique code associated with this invitation', unique=True, max_length=64, verbose_name='Secret')),
                ('host', models.CharField(help_text='The host this invitation was created on', max_length=32)),
                ('user_group', models.CharField(default='V', max_length=1, verbose_name='User Role', choices=[('A', 'Administrator'), ('E', 'Editor'), ('V', 'Viewer')])),
                ('created_by', models.ForeignKey(related_name=b'orgs_invitation_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item')),
                ('modified_by', models.ForeignKey(related_name=b'orgs_invitation_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item')),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='Language',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('name', models.CharField(max_length=128)),
                ('iso_code', models.CharField(max_length=4)),
                ('created_by', models.ForeignKey(related_name=b'orgs_language_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item')),
                ('modified_by', models.ForeignKey(related_name=b'orgs_language_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item')),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='Org',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('name', models.CharField(max_length=128, verbose_name='Name')),
                ('plan', models.CharField(default='FREE', help_text='What plan your organization is on', max_length=16, verbose_name='Plan', choices=[('FREE', 'Free Plan'), ('TRIAL', 'Trial'), ('TIER_39', 'Bronze'), ('TIER1', 'Silver'), ('TIER2', 'Gold (Legacy)'), ('TIER3', 'Platinum (Legacy)'), ('TIER_249', 'Gold'), ('TIER_449', 'Platinum')])),
                ('plan_start', models.DateTimeField(help_text='When the user switched to this plan', verbose_name='Plan Start', auto_now_add=True)),
                ('stripe_customer', models.CharField(help_text='Our Stripe customer id for your organization', max_length=32, null=True, verbose_name='Stripe Customer', blank=True)),
                ('language', models.CharField(choices=[(b'en-us', b'English'), (b'pt-br', b'Portuguese'), (b'fr', b'French'), (b'es', b'Spanish')], max_length=64, blank=True, help_text='The main language used by this organization', null=True, verbose_name='Language')),
                ('timezone', models.CharField(max_length=64, verbose_name='Timezone')),
                ('date_format', models.CharField(default='D', help_text='Whether day comes first or month comes first in dates', max_length=1, verbose_name='Date Format', choices=[('D', 'DD-MM-YYYY'), ('M', 'MM-DD-YYYY')])),
                ('webhook', models.CharField(max_length=255, null=True, verbose_name='Webhook', blank=True)),
                ('webhook_events', models.IntegerField(default=0, help_text='Which type of actions will trigger webhook events.', verbose_name='Webhook Events')),
                ('msg_last_viewed', models.DateTimeField(auto_now_add=True, verbose_name='Message Last Viewed')),
                ('flows_last_viewed', models.DateTimeField(auto_now_add=True, verbose_name='Flows Last Viewed')),
                ('config', models.TextField(help_text='More Organization specific configuration', null=True, verbose_name='Configuration')),
                ('slug', models.SlugField(null=True, error_messages={b'unique': 'This slug is not available'}, max_length=255, blank=True, unique=True, verbose_name='Slug')),
                ('is_anon', models.BooleanField(default=False, help_text='Whether this organization anonymizes the phone numbers of contacts within it')),
                ('administrators', models.ManyToManyField(help_text='The administrators in your organization', related_name='org_admins', verbose_name='Administrators', to=settings.AUTH_USER_MODEL)),
                ('country', models.ForeignKey(on_delete=django.db.models.deletion.SET_NULL, blank=True, to='locations.AdminBoundary', help_text='The country this organization should map results for.', null=True)),
                ('created_by', models.ForeignKey(related_name=b'orgs_org_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item')),
                ('editors', models.ManyToManyField(help_text='The editors in your organization', related_name='org_editors', verbose_name='Editors', to=settings.AUTH_USER_MODEL)),
                ('modified_by', models.ForeignKey(related_name=b'orgs_org_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item')),
                ('primary_language', models.ForeignKey(related_name='orgs', on_delete=django.db.models.deletion.SET_NULL, blank=True, to='orgs.Language', help_text='The primary language will be used for contacts with no language preference.', null=True)),
                ('viewers', models.ManyToManyField(help_text='The viewers in your organization', related_name='org_viewers', verbose_name='Viewers', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='TopUp',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('price', models.IntegerField(help_text='The price paid for the messages in this top up (in cents)', verbose_name='Price Paid')),
                ('credits', models.IntegerField(help_text='The number of credits bought in this top up', verbose_name='Number of Credits')),
                ('expires_on', models.DateTimeField(help_text='The date that this top up will expire', verbose_name='Expiration Date')),
                ('stripe_charge', models.CharField(help_text='The Stripe charge id for this charge', max_length=32, null=True, verbose_name='Stripe Charge Id', blank=True)),
                ('comment', models.CharField(help_text='Any comment associated with this topup, used when we credit accounts', max_length=255, null=True, blank=True)),
                ('created_by', models.ForeignKey(related_name=b'orgs_topup_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item')),
                ('modified_by', models.ForeignKey(related_name=b'orgs_topup_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item')),
                ('org', models.ForeignKey(related_name='topups', to='orgs.Org', help_text='The organization that was toppped up')),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='UserSettings',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('language', models.CharField(default='en-us', help_text='Your preferred language', max_length=8, choices=[(b'en-us', b'English'), (b'pt-br', b'Portuguese'), (b'fr', b'French'), (b'es', b'Spanish')])),
                ('tel', models.CharField(help_text='Phone number for testing and recording voice flows', max_length=16, null=True, verbose_name='Phone Number', blank=True)),
                ('user', models.ForeignKey(related_name='settings', to=settings.AUTH_USER_MODEL)),
            ],
            options={
            },
            bases=(models.Model,),
        ),
        migrations.AddField(
            model_name='language',
            name='org',
            field=models.ForeignKey(related_name='languages', verbose_name='Org', to='orgs.Org'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='invitation',
            name='org',
            field=models.ForeignKey(related_name='invitations', verbose_name='Org', to='orgs.Org', help_text='The organization to which the account is invited to view'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='creditalert',
            name='org',
            field=models.ForeignKey(help_text='The organization this alert was triggered for', to='orgs.Org'),
            preserve_default=True,
        ),
    ]
