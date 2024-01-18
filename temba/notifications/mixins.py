from .models import Notification


class NotificationTargetMixin:
    """
    Mixin for views which can be targets of notifications to help them clear unseen notifications. This is defined in a
    separate module to views.py to avoid creating a circular dependency with orgs/views.py
    """

    notification_type = None
    notification_scope = ""

    def get_notification_scope(self) -> tuple[str, str]:  # pragma: no cover
        return self.notification_type, self.notification_scope

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)

        notification_type, scope = self.get_notification_scope()
        if request.org and notification_type and request.user.is_authenticated:
            Notification.mark_seen(request.org, notification_type, scope=scope, user=request.user)

        return response
