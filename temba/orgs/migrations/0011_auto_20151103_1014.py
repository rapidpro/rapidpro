# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


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
        migrations.AlterField(
            model_name='invitation',
            name='user_group',
            field=models.CharField(default='V', max_length=1, verbose_name='User Role', choices=[('A', 'Administrator'), ('E', 'Editor'), ('V', 'Viewer'), ('S', 'Surveyor')]),
        ),
    ]
