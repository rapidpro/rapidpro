# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0010_auto_20151002_1417'),
    ]

    operations = [
        migrations.AlterField(
            model_name='invitation',
            name='email',
            field=models.EmailField(help_text='The email to which we send the invitation of the viewer', max_length=254, verbose_name='Email'),
        ),
        migrations.AddField(
            model_name='org',
            name='brand',
            field=models.CharField(default=b'rapidpro.io', help_text='The brand used in emails', max_length=128, verbose_name='Brand'),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='invitation',
            name='user_group',
            field=models.CharField(default='V', max_length=1, verbose_name='User Role', choices=[('A', 'Administrator'), ('E', 'Editor'), ('V', 'Viewer'), ('S', 'Surveyor')]),
            preserve_default=True,
        ),
    ]
