# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('orgs', '0020_auto_20160726_1510'),
    ]

    operations = [
        migrations.CreateModel(
            name='Debit',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text='Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text='When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text='When this item was last modified', auto_now=True)),
                ('amount', models.IntegerField(help_text='How many credits were debited')),
                ('debit_type', models.CharField(help_text='What caused this debit', max_length=1, choices=[('A', 'Allocation'), ('P', 'Purge')])),
                ('beneficiary', models.ForeignKey(related_name='allocations', to='orgs.TopUp', help_text='Optional topup that was allocated with these credits', null=True)),
                ('created_by', models.ForeignKey(related_name='orgs_debit_creations', to=settings.AUTH_USER_MODEL, help_text='The user which originally created this item')),
                ('modified_by', models.ForeignKey(related_name='orgs_debit_modifications', to=settings.AUTH_USER_MODEL, help_text='The user which last modified this item')),
                ('topup', models.ForeignKey(related_name='debits', to='orgs.TopUp', help_text='The topup these credits are applied against')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.AddField(
            model_name='org',
            name='multi_org',
            field=models.BooleanField(default=False, help_text='Put this org on the multi org level'),
        ),
        migrations.AddField(
            model_name='org',
            name='parent',
            field=models.ForeignKey(blank=True, to='orgs.Org', help_text='The parent org that manages this org', null=True),
        ),
    ]
