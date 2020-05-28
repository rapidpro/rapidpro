from django.db import models
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from django.utils.timesince import timesince

from temba.migrator import Migrator
from temba.utils import json
from temba.utils.models import TembaModel


class MigrationTask(TembaModel):
    STATUS_PENDING = "P"
    STATUS_PROCESSING = "O"
    STATUS_COMPLETE = "C"
    STATUS_FAILED = "F"
    STATUS_CHOICES = (
        (STATUS_PENDING, _("Pending")),
        (STATUS_PROCESSING, _("Processing")),
        (STATUS_COMPLETE, _("Complete")),
        (STATUS_FAILED, _("Failed")),
    )

    org = models.ForeignKey(
        "orgs.Org",
        on_delete=models.PROTECT,
        related_name="%(class)ss",
        help_text=_("The organization of this import progress."),
    )

    migration_org = models.PositiveIntegerField(
        verbose_name=_("Org ID"), help_text=_("The organization ID on live server that is being migrated")
    )

    status = models.CharField(max_length=1, default=STATUS_PENDING, choices=STATUS_CHOICES)

    @classmethod
    def create(cls, org, user, migration_org):
        return cls.objects.create(org=org, migration_org=migration_org, created_by=user, modified_by=user)

    def update_status(self, status):
        self.status = status
        self.save(update_fields=("status", "modified_on"))

    def perform(self):
        start = timezone.now()

        migrator = Migrator(org_id=self.migration_org)

        # Updating organization data
        org_data = migrator.get_org()
        if org_data:
            self.update_org(org_data)

        # TODO show this elapsed
        elapsed = timesince(start)

        self.update_status(self.STATUS_COMPLETE)

    def update_org(self, org_data):
        self.org.name = org_data.name
        self.org.plan = org_data.plan
        self.org.plan_start = org_data.plan_start
        self.org.stripe_customer = org_data.stripe_customer
        self.org.language = org_data.language
        self.org.timezone = org_data.timezone
        self.org.date_format = org_data.date_format
        self.org.config = json.loads(org_data.config) if org_data.config else dict()
        self.org.is_anon = org_data.is_anon
        self.org.surveyor_password = org_data.surveyor_password
        self.org.parent_id = org_data.parent_id
        self.org.save(
            update_fields=[
                "name",
                "plan",
                "plan_start",
                "stripe_customer",
                "language",
                "timezone",
                "date_format",
                "config",
                "is_anon",
                "surveyor_password",
                "parent_id",
            ]
        )
