# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations


INDEX_SQL = """
DO $$
BEGIN

IF NOT EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relname = 'org_test_contacts' AND n.nspname = 'public') THEN
    CREATE INDEX org_test_contacts ON contacts_contact (org_id) WHERE is_test = TRUE;
END IF;

END$$;"""


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0027_auto_20151103_1014'),
    ]

    operations = [
        migrations.RunSQL(INDEX_SQL)
    ]
