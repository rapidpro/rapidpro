# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0004_merge'),
    ]

    operations = [
        migrations.RunSQL("""
                          CREATE OR REPLACE FUNCTION charfield_to_hstore(varchar)
                            RETURNS hstore
                            IMMUTABLE
                            STRICT
                            LANGUAGE sql
                          AS $func$
                            SELECT hstore('url', $1)
                          $func$;
                          """),

        migrations.RunSQL("ALTER TABLE orgs_org ALTER COLUMN webhook SET DATA TYPE hstore USING charfield_to_hstore(webhook || '');")
    ]
