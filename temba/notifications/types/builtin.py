from django.urls import reverse
from django.utils.translation import gettext_lazy as _

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
            medium=Notification.MEDIUM_UI + Notification.MEDIUM_EMAIL,
            email_status=Notification.EMAIL_STATUS_PENDING,
            **{"export": export},
        )

    def get_target_url(self, notification) -> str:
        return reverse("orgs.export_download", kwargs={"uuid": notification.export.uuid})

    def get_email_subject(self, notification) -> str:
        return _("Your %s export is ready") % notification.export.notification_export_type

    def get_email_template(self, notification) -> str:
        return "notifications/email/export_finished"

    def as_json(self, notification) -> dict:
        json = super().as_json(notification)
        json["export"] = {
            "type": notification.export.notification_export_type,
            "num_records": notification.export.num_records,
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


class UserEmailNotificationType(NotificationType):
    """
    Notification that a user's email has been changed.
    """

    slug = "user:email"

    @classmethod
    def create(cls, org, user, prev_email: str):
        Notification.create_all(
            org,
            cls.slug,
            scope=str(user.id),
            users=[user],
            medium=Notification.MEDIUM_EMAIL,
            email_status=Notification.EMAIL_STATUS_PENDING,
            email_address=prev_email,
        )

    def get_target_url(self, notification) -> str:
        pass

    def get_email_subject(self, notification) -> str:
        return _("Your email has been changed")

    def get_email_template(self, notification) -> str:
        return "notifications/email/user_email"


class UserPasswordNotificationType(NotificationType):
    """
    Notification that a user's password has been changed.
    """

    slug = "user:password"

    @classmethod
    def create(cls, org, user):
        Notification.create_all(
            org,
            cls.slug,
            scope=str(user.id),
            users=[user],
            medium=Notification.MEDIUM_EMAIL,
            email_status=Notification.EMAIL_STATUS_PENDING,
        )

    def get_target_url(self, notification) -> str:
        pass

    def get_email_subject(self, notification) -> str:
        return _("Your password has been changed")

    def get_email_template(self, notification) -> str:
        return "notifications/email/user_password"


class InvitationAcceptedNotificationType(NotificationType):
    """
    Notification that a user accepted an invitation to join the workspace.
    """

    slug = "invitation:accepted"

    @classmethod
    def create(cls, invitation, new_user):
        """
        Creates a user joined notification for all admins in the workspace.
        """

        Notification.create_all(
            invitation.org,
            cls.slug,
            scope=str(invitation.id),
            users=invitation.org.get_admins().exclude(id=new_user.id),
            medium=Notification.MEDIUM_EMAIL,
            email_status=Notification.EMAIL_STATUS_PENDING,
            data={"email": invitation.email},
        )

    def get_target_url(self, notification) -> str:
        pass

    def get_email_subject(self, notification) -> str:
        return _("New user joined your workspace")

    def get_email_template(self, notification) -> str:
        return "notifications/email/invitation_accepted"
