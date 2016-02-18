# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.conf import settings


class Migration(migrations.Migration):

    def populate_orgs_brand(apps, schema_editor):
        Org = apps.get_model("orgs", "Org")
        brand = settings.HOSTNAME
        if brand not in settings.BRANDING.keys():
            brand = settings.DEFAULT_BRAND

        Org.objects.all().update(brand=brand)

    dependencies = [
        ('orgs', '0011_auto_20151026_1146'),
    ]

    operations = [
        migrations.RunPython(populate_orgs_brand),
        migrations.AlterField(
            model_name='invitation',
            name='user_group',
            field=models.CharField(default='V', max_length=1, verbose_name='User Role', choices=[('A', 'Administrator'), ('E', 'Editor'), ('V', 'Viewer'), ('S', 'Surveyor')]),
        ),
    ]
