# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import os

from django.db.migrations import RunSQL


class InstallSQL(RunSQL):
    """
    Migration that reads the SQL from the named file and runs it as a RunSQL migration
    """
    def __init__(self, filename):
        # build the full path to our filename
        sql_path = os.path.join(os.path.dirname(__file__), '%s.sql' % filename)
        with open(sql_path) as sql_file:
            sql = sql_file.read()

        super(InstallSQL, self).__init__(sql)
