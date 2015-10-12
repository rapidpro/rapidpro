# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('values', '0004_auto_20150728_1030'),
    ]

    operations = [
        migrations.RunSQL(
            "CREATE INDEX values_value_contact_field_location_not_null "
            "ON values_value(contact_field_id, location_value_id) "
            "WHERE contact_field_id IS NOT NULL AND location_value_id IS NOT NULL;",
            "DROP INDEX values_value_contact_field_location_not_null;")
    ]
