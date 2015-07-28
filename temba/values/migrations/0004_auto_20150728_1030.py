# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


def fix_district_contact_fields_values(apps, schema_editor):
    Value = apps.get_model('values', 'Value')
    ContactField = apps.get_model('contacts', 'ContactField')

    for district_value in Value.objects.filter(contact_field__value_type='I'):
        org = district_value.org
        contact = district_value.contact

        state_field = ContactField.objects.filter(is_active=True, org=org, value_type='S').first()
        state_field_key = state_field.key

        state_value = Value.objects.filter(contact=contact, contact_field__key__exact=state_field_key).first()

        if district_value.location_value.parent != state_value.location_value:
            new_district_boundary = state_value.location_value.children.filter(
                name__iexact=district_value.location_value.name,
                level=2).first()

            if new_district_boundary:
                district_value.location_value = new_district_boundary
                district_value.save()
            else:
                state_value.location_value = district_value.location_value.parent
                state_value.save()


class Migration(migrations.Migration):

    dependencies = [
        ('values', '0003_auto_20150527_1909'),
    ]

    operations = [
        migrations.RunPython(fix_district_contact_fields_values)
    ]
