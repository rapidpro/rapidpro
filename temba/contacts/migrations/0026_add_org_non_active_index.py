# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations

class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0025_unblock_contacts_imported_again_after_being_blocked'),
    ]

    operations = [
        migrations.RunSQL("""CREATE INDEX contacts_contact_inactive_org_contacts
                             ON contacts_contact(org_id, is_active) WHERE is_active = FALSE;""",
                          """DROP INDEX contacts_contact_inactive_org_contacts;"""),
        ]
