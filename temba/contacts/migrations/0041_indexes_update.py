# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations


INDEX_SQL = """
-- for the API view of active contacts
CREATE INDEX contacts_contact_org_modified_id_where_nontest_active
ON contacts_contact (org_id, modified_on DESC, id DESC)
WHERE is_test = false AND is_active = true;

DROP INDEX IF EXISTS contacts_contact_org_id_modified_on_active;

-- for the API view of deleted contacts
CREATE INDEX contacts_contact_org_modified_id_where_nontest_inactive
ON contacts_contact (org_id, modified_on DESC, id DESC)
WHERE is_test = false AND is_active = false;

DROP INDEX IF EXISTS contacts_contact_org_id_modified_on_inactive;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0040_rename_groups_key'),
    ]

    operations = [
        migrations.RunSQL(INDEX_SQL)
    ]
