# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations

INDEX_SQL = """
DO $$
BEGIN

DROP INDEX IF EXISTS contacts_contact_inactive_org_contacts;

IF NOT EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE c.relname = 'contacts_contact_org_id_modified_on_active' AND n.nspname = 'public') THEN
    CREATE INDEX contacts_contact_org_id_modified_on_active ON contacts_contact (org_id, modified_on DESC)
      WHERE is_test = false AND is_active = true;
END IF;

IF NOT EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE c.relname = 'contacts_contact_org_id_modified_on_inactive' AND n.nspname = 'public') THEN
    CREATE INDEX contacts_contact_org_id_modified_on_inactive ON contacts_contact (org_id, modified_on DESC)
      WHERE is_test = false AND is_active = false;
END IF;

END$$;"""


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0031_contactfield_audit_fields'),
    ]

    operations = [
        migrations.RunSQL(INDEX_SQL)
    ]
