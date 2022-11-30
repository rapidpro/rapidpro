from django.urls import reverse

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

        Notification.create_all(
            export.org,
            cls.slug,
            scope=export.get_notification_scope(),
            users=[export.created_by],
            email_status=Notification.EMAIL_STATUS_PENDING,
            **{export.notification_export_type + "_export": export},
        )

    def get_target_url(self, notification) -> str:
        return notification.export.get_download_url()

    def get_email_subject(self, notification) -> str:
        return f"Your {notification.export.notification_export_type} export is ready"

    def get_email_template(self, notification) -> str:
        return f"notifications/email/export_finished.{notification.export.notification_export_type}"

    def get_email_context(self, notification):
        context = super().get_email_context(notification)
        if notification.results_export:
            context["flows"] = notification.results_export.flows.order_by("name")
        return context

    def as_json(self, notification) -> dict:
        json = super().as_json(notification)
        json["export"] = {"type": notification.export.notification_export_type}
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
            scope=str(incident.id),
            users=incident.org.get_admins(),
            email_status=Notification.EMAIL_STATUS_NONE,  # TODO add email support
            incident=incident,
        )

    def get_target_url(self, notification) -> str:
        return reverse("notifications.incident_list")

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
