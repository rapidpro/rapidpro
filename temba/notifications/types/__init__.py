from .builtin import (
    ExportFinishedNotificationType,
    ImportFinishedNotificationType,
    IncidentStartedNotificationType,
    TicketActivityNotificationType,
    TicketsOpenedNotificationType,
    UserEmailNotificationType,
    UserPasswordNotificationType,
)

TYPES = {}


def register_notification_type(typ):
    """
    Registers a notification type
    """
    global TYPES

    assert typ.slug not in TYPES, f"type {typ.slug} is already registered"

    TYPES[typ.slug] = typ


register_notification_type(ExportFinishedNotificationType())
register_notification_type(ImportFinishedNotificationType())
register_notification_type(IncidentStartedNotificationType())
register_notification_type(TicketsOpenedNotificationType())
register_notification_type(TicketActivityNotificationType())
register_notification_type(UserEmailNotificationType())
register_notification_type(UserPasswordNotificationType())
