from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from temba.orgs.models import Export

from ..models import Notification, NotificationType


class ExportFinishedNotificationType(NotificationType):
    """
    Notification that an export has finished
    """

    slug = "export:finished"

    @classmethod
    def create(cls, export):
        """
        Creates an export finished notification for the creator of the given export.
        """

        export_field = "export" if isinstance(export, Export) else export.notification_export_type + "_export"

        Notification.create_all(
            export.org,
            cls.slug,
            scope=export.get_notification_scope(),
            users=[export.created_by],
            medium=Notification.MEDIUM_UI + Notification.MEDIUM_EMAIL,
            email_status=Notification.EMAIL_STATUS_PENDING,
            **{export_field: export},
        )

    def get_target_url(self, notification) -> str:
        # if legacy export model, call model method
        if not notification.export:
            return notification.export_obj.get_download_url()

        return reverse("orgs.export_download", kwargs={"uuid": notification.export.uuid})

    def get_email_subject(self, notification) -> str:
        return _("Your %s export is ready") % notification.export_obj.notification_export_type

    def get_email_template(self, notification) -> str:
        return f"notifications/email/export_finished.{notification.export_obj.notification_export_type}"

    def get_email_context(self, notification):
        context = super().get_email_context(notification)
        if notification.results_export:
            context["flows"] = notification.results_export.flows.order_by("name")
        return context

    def as_json(self, notification) -> dict:
        json = super().as_json(notification)
        json["export"] = {
            "type": notification.export_obj.notification_export_type,
            "num_records": notification.export_obj.num_records,
        }
        return json


class ImportFinishedNotificationType(NotificationType):
    """
    Notification that an import has finished - created by mailroom
    """

    slug = "import:finished"

    def get_target_url(self, notification) -> str:
        return reverse("contacts.contactimport_read", args=[notification.contact_import.id])

    def as_json(self, notification) -> dict:
        json = super().as_json(notification)
        json["import"] = {"type": "contact", "num_records": notification.contact_import.num_records}
        return json


class IncidentStartedNotificationType(NotificationType):
    """
    Notification that an incident has started - created locally or in mailroom.
    """

    slug = "incident:started"

    @classmethod
    def create(cls, incident):
        """
        Creates an incident started notification for all admins in the workspace.
        """

        Notification.create_all(
            incident.org,
            cls.slug,
            scope=incident.type.get_notification_scope(incident),
            users=incident.org.get_admins(),
            medium=Notification.MEDIUM_UI + Notification.MEDIUM_EMAIL,
            email_status=Notification.EMAIL_STATUS_PENDING,
            incident=incident,
        )

    def get_target_url(self, notification) -> str:
        return notification.incident.type.get_notification_target_url(notification.incident)

    def get_email_subject(self, notification) -> str:
        return _("Incident") + ": " + notification.incident.type.title

    def get_email_template(self, notification) -> str:
        return notification.incident.email_template

    def as_json(self, notification) -> dict:
        json = super().as_json(notification)
        json["incident"] = notification.incident.as_json()
        return json


class TicketsOpenedNotificationType(NotificationType):
    """
    Notification that a new ticket has been opened - created by mailroom.
    """

    slug = "tickets:opened"

    def get_target_url(self, notification) -> str:
        return "/ticket/unassigned/"


class TicketActivityNotificationType(NotificationType):
    """
    Notification of activity on tickets assigned to a user - created by mailroom.
    """

    slug = "tickets:activity"

    def get_target_url(self, notification) -> str:
        return "/ticket/mine/"
