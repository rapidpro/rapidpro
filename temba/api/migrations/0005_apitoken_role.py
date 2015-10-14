# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from temba.api.models import APIToken

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0004_webhookresult_request'),
    ]

    def populate_token_roles(apps, schema_editor):
        for token in APIToken.objects.all():
            group = token.org.get_user_org_group(token.user)

            if group:
                token.role = group.name[0:1]  # S, A, V, E
                token.save()
            else:
                print "Removing abandoned token for %s: %s (%s)" % (token.user, token.org, token.key)
                token.delete()

    operations = [
        migrations.AddField(
            model_name='apitoken',
            name='role',
            field=models.CharField(max_length=1, null=True),
            preserve_default=True,
        ),
        migrations.RunPython(populate_token_roles, populate_token_roles),
        migrations.AlterField(
            model_name='apitoken',
            name='role',
            field=models.CharField(max_length=1),
            preserve_default=True,
        ),
        migrations.AlterUniqueTogether(
            name='apitoken',
            unique_together=set([('user', 'org', 'role')]),
        ),
    ]
