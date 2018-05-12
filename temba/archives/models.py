from django.db import models

from gettext import gettext as _
from django.utils import timezone


class Archive(models.Model):
    TYPE_MSG = 'msg'
    TYPE_FLOWRUN = 'flowrun'
    TYPE_SESSION = 'session'

    TYPE_CHOICES = (
        (TYPE_MSG, _("Message")),
        (TYPE_FLOWRUN, _("Flow Runs")),
        (TYPE_SESSION, _("Session")),
    )

    org = models.ForeignKey('orgs.Org', db_constraint=False,
                            help_text="The org this archive is for")
    archive_type = models.CharField(choices=TYPE_CHOICES, max_length=16,
                                    help_text="The type of record this is an archive for")
    created_on = models.DateTimeField(default=timezone.now,
                                      help_text="When this archive was created")

    start_date = models.DateField(help_text="The starting modified_on date for records in this archive (inclusive")
    end_date = models.DateField(help_text="The ending modified_on date for records in this archive (exclusive)")

    record_count = models.IntegerField(default=0,
                                       help_text="The number of records in this archive")

    archive_size = models.IntegerField(default=0,
                                       help_text="The size of this archive in bytes (after gzipping)")
    archive_hash = models.TextField(help_text="The md5 hash of this archive (after gzipping)")
    archive_url = models.URLField(help_text="The full URL for this archive")

    is_purged = models.BooleanField(default=False,
                                    help_text="Whether the records in this archive have been purged from the database")
    build_time = models.IntegerField(help_text="The number of milliseconds it took to build and upload this archive")
