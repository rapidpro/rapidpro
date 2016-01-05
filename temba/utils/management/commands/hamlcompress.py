from __future__ import absolute_import, unicode_literals

from compressor.management.commands.compress import Command as CompressCommand


class Command(CompressCommand):
    """
    Defer to the real compress command in django_compressor. This subclass is only for backward compatibility so that
    we can still invoke it as hamlcompress
    """
    pass
