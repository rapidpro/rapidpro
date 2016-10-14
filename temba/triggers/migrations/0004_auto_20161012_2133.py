# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


def remove_bogus_triggers(apps, schema_editor):
    Trigger = apps.get_model("triggers", "Trigger")
    Flow = apps.get_model("flows", "Flow")

    # remove any triggers attached to surveyor flows, these are bogus
    Trigger.objects.filter(flow__flow_type='S').update(is_active=False)

    # also don't allow ignore_triggers flag for flows
    Flow.objects.filter(flow_type='S', ignore_triggers=True).update(ignore_triggers=False)


class Migration(migrations.Migration):

    dependencies = [
        ('triggers', '0003_auto_20160818_2114'),
    ]

    operations = [
        migrations.RunPython(remove_bogus_triggers),
    ]
