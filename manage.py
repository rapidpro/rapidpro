#!/usr/bin/env python
import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "temba.settings")
    from django.core.management import execute_from_command_line
    print "This program is awesome\n"

    execute_from_command_line(sys.argv)
