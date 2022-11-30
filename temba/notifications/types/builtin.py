from django.urls import reverse

from ..models import NotificationType


class ExportFinishedNotificationType(NotificationType):
    slug = "export:finished"

    def get_target_url(self, notification) -> str:
        return notification.export.get_download_url()

    def get_email_template(self, notification) -> tuple:
        export_type = notification.export.notification_export_type
        return f"Your {export_type} export is ready", f"notifications/email/export_finished.{export_type}"

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
    slug = "import:finished"

    def get_target_url(self, notification) -> str:
        return reverse("contacts.contactimport_read", args=[notification.contact_import.id])

    def as_json(self, notification) -> dict:
        json = super().as_json(notification)
        json["import"] = {"type": "contact", "num_records": notification.contact_import.num_records}
        return json


class IncidentStartedNotificationType(NotificationType):
    slug = "incident:started"

    def get_target_url(self, notification) -> str:
        return reverse("notifications.incident_list")

    def as_json(self, notification) -> dict:
        json = super().as_json(notification)
        json["incident"] = notification.incident.as_json()
        return json


class TicketsOpenedNotificationType(NotificationType):
    slug = "tickets:opened"

    def get_target_url(self, notification) -> str:
        return "/ticket/unassigned/"


class TicketActivityNotificationType(NotificationType):
    slug = "tickets:activity"

    def get_target_url(self, notification) -> str:
        return "/ticket/mine/"
