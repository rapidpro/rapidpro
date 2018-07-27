# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
    ]

    operations = [
        migrations.RunSQL("ALTER TABLE auth_user ALTER COLUMN username TYPE VARCHAR(254);")
    ]
