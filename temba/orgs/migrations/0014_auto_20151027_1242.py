# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    def populate_alert_type(apps, schema_editor):
        CreditAlert = apps.get_model('orgs', 'CreditAlert')
        CreditAlert.objects.filter(threshold__gt=0, threshold__lte=500).update(alert_type='L')
        CreditAlert.objects.filter(threshold__lte=0).update(alert_type='O')


    dependencies = [
        ('orgs', '0013_creditalert_alert_type'),
    ]

    operations = [
        migrations.RunPython(populate_alert_type),
    ]
