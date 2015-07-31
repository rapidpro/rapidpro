# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    # language=SQL
    CREATE_INDEX = """
    CREATE INDEX flows_flowrun_expires_on ON flows_flowrun(expires_on) WHERE is_active = TRUE;
    """

    # language=SQL
    REMOVE_INDEX = """
    DROP INDEX flows_flowrun_expires_on;
    """

    dependencies = [
        ('flows', '0024_advance_stuck_runs'),
    ]

    operations = [
        migrations.RunSQL(CREATE_INDEX, REMOVE_INDEX)
    ]
