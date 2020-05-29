import os
import logging

from django.db import models
from django.conf import settings
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from django.utils.timesince import timesince

from temba.migrator import Migrator
from temba.orgs.models import TopUp
from temba.utils import json
from temba.utils.models import TembaModel

logger = logging.getLogger(__name__)


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
        migration_folder = f"{settings.MEDIA_ROOT}/migration_logs"
        if not os.path.exists(migration_folder):
            os.makedirs(migration_folder)

        log_handler = logging.FileHandler(filename=f"{migration_folder}/{self.uuid}.log")
        logger.addHandler(log_handler)
        logger.setLevel("INFO")

        start = timezone.now()

        migrator = Migrator(org_id=self.migration_org)

        logger.info("---------------- Organization ----------------")
        logger.info("[STARTED] Organization data migration")

        org_data = migrator.get_org()
        if not org_data:
            logger.info("[ERROR] No organization data found")
            self.update_status(self.STATUS_FAILED)
            return

        self.update_org(org_data)
        logger.info("[COMPLETED] Organization data migration")

        logger.info("")
        logger.info("---------------- Organization TopUps ----------------")
        logger.info("[STARTED] Organization TopUps migration")

        # Inactivating all org topups before importing the ones from Live server
        TopUp.objects.filter(is_active=True, org=self.org).update(is_active=False)

        org_topups = migrator.get_org_topups()
        if org_topups:
            self.add_topups(logger=logger, topups=org_topups)

        logger.info("[COMPLETED] Organization TopUps migration")

        logger.info("")
        elapsed = timesince(start)
        logger.info(f"This process took {elapsed}")

        self.update_status(self.STATUS_COMPLETE)

    def update_org(self, org_data):
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

    def add_topups(self, logger, topups):
        for topup in topups:
            logger.info(f">>> TopUp: {topup.id} - {topup.credits}")
            TopUp.create(
                user=self.created_by,
                price=topup.price,
                credits=topup.credits,
                org=self.org,
                expires_on=topup.expires_on,
            )
