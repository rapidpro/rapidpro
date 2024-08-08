import hmac
import logging
from hashlib import sha1

from rest_framework.permissions import BasePermission
from smartmin.models import SmartModel

from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba.orgs.models import Org, OrgRole, User
from temba.utils.models import JSONAsTextField
from temba.utils.uuid import uuid4

logger = logging.getLogger(__name__)


class BulkActionFailure:
    """
    Bulk action serializers can return a partial failure if some objects couldn't be acted on
    """

    def __init__(self, failures):
        self.failures = failures

    def as_json(self):
        return {"failures": self.failures}


class APIPermission(BasePermission):
    """
    Verifies that the user has the permission set on the endpoint view
    """

    perms_map = {
        "GET": "%(app_label)s.%(model_name)s_list",
        "POST": "%(app_label)s.%(model_name)s_create",
        "PUT": "%(app_label)s.%(model_name)s_update",
        "DELETE": "%(app_label)s.%(model_name)s_delete",
    }

    def get_required_permission(self, request, view) -> str:
        """
        Given a model and an HTTP method, return the list of permission
        codes that the user is required to have.
        """

        if hasattr(view, "permission"):
            return view.permission

        if request.method not in self.perms_map or request.method not in view.allowed_methods:
            view.http_method_not_allowed(request)

        return self.perms_map[request.method] % {
            "app_label": view.model._meta.app_label,
            "model_name": view.model._meta.model_name,
        }

    def has_permission(self, request, view):
        # viewing docs is always allowed
        if view.is_docs():
            return request.method == "GET"

        permission = self.get_required_permission(request, view)

        # no anon access to API endpoints
        if request.user.is_anonymous:
            return False

        org = request.org

        if request.auth:
            # check that user is still allowed to use the token's role
            if not request.auth.is_valid():
                return False

            role = OrgRole.from_group(request.auth.role)
        elif org:
            # user may not have used token authentication
            role = org.get_user_role(request.user)
        else:
            return False

        has_perm = role.has_api_perm(permission)

        # viewers can only ever get from the API
        if role == OrgRole.VIEWER:
            return has_perm and request.method == "GET"

        return has_perm


class SSLPermission(BasePermission):  # pragma: no cover
    """
    Verifies that the request used SSL if that is required
    """

    def has_permission(self, request, view):
        if getattr(settings, "SESSION_COOKIE_SECURE", False):
            return request.is_secure()
        else:
            return True


class Resthook(SmartModel):
    """
    Represents a hook that a user creates on an organization. Outside apps can integrate by subscribing
    to this particular resthook.
    """

    org = models.ForeignKey(
        Org,
        on_delete=models.PROTECT,
        related_name="resthooks",
        help_text=_("The organization this resthook belongs to"),
    )

    slug = models.SlugField(help_text=_("A simple label for this event"))

    @classmethod
    def get_or_create(cls, org, slug, user):
        """
        Looks up (or creates) the resthook for the passed in org and slug
        """
        slug = slug.lower().strip()
        resthook = Resthook.objects.filter(is_active=True, org=org, slug=slug).first()
        if not resthook:
            resthook = Resthook.objects.create(org=org, slug=slug, created_by=user, modified_by=user)

        return resthook

    def add_subscriber(self, url, user):
        subscriber = self.subscribers.create(target_url=url, created_by=user, modified_by=user)
        self.modified_by = user
        self.save(update_fields=["modified_on", "modified_by"])
        return subscriber

    def release(self, user):
        # release any active subscribers
        for s in self.subscribers.filter(is_active=True):
            s.release(user)

        # then ourselves
        self.is_active = False
        self.modified_by = user
        self.save(update_fields=["is_active", "modified_on", "modified_by"])

    def delete(self):
        self.subscribers.all().delete()

        super().delete()

    def __str__(self):  # pragma: needs cover
        return str(self.slug)


class ResthookSubscriber(SmartModel):
    """
    Represents a subscriber on a specific resthook within one of our flows.
    """

    resthook = models.ForeignKey(
        Resthook, on_delete=models.PROTECT, related_name="subscribers", help_text=_("The resthook being subscribed to")
    )

    target_url = models.URLField(help_text=_("The URL that we will call when our ruleset is reached"))

    def as_json(self):  # pragma: needs cover
        return dict(id=self.id, resthook=self.resthook.slug, target_url=self.target_url, created_on=self.created_on)

    def release(self, user):
        self.is_active = False
        self.modified_by = user
        self.save(update_fields=["is_active", "modified_on", "modified_by"])

        # update our parent as well
        self.resthook.modified_by = user
        self.resthook.save(update_fields=["modified_on", "modified_by"])


class WebHookEvent(models.Model):
    """
    Represents a payload to be sent to a resthook
    """

    # the organization this event is tied to
    org = models.ForeignKey(Org, on_delete=models.PROTECT)

    # the resthook this event is for
    resthook = models.ForeignKey(Resthook, on_delete=models.PROTECT)

    # the data that would have been POSTed to this event
    data = JSONAsTextField(default=dict)

    # the method for our request
    action = models.CharField(max_length=8, default="POST")

    # when this event was created
    created_on = models.DateTimeField(default=timezone.now)


class APIToken(models.Model):
    """
    An org+user+role specific access token for the API
    """

    GROUP_GRANTED_TO = {
        "Administrators": (OrgRole.ADMINISTRATOR,),
        "Editors": (OrgRole.ADMINISTRATOR, OrgRole.EDITOR),
    }

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="api_tokens")
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="api_tokens")
    role = models.ForeignKey(Group, on_delete=models.PROTECT)
    key = models.CharField(max_length=40, primary_key=True)
    created = models.DateTimeField(default=timezone.now)
    is_active = models.BooleanField(default=True)

    @classmethod
    def get_or_create(cls, org, user, *, role: OrgRole = None, refresh: bool = False):
        """
        Gets or creates an API token for this user
        """

        role = role or cls.get_default_role(org, user)
        role_group = role.group if role else None

        if not role_group:
            raise ValueError("User '%s' has no suitable role for API usage" % str(user))
        elif role_group.name not in cls.GROUP_GRANTED_TO:
            raise ValueError("Role %s is not valid for API usage" % role_group.name)

        tokens = cls.objects.filter(is_active=True, user=user, org=org, role=role_group)

        # if we are refreshing the token, clear existing ones
        if refresh and tokens:
            for token in tokens:
                token.release()
            tokens = None

        if not tokens:
            return cls.objects.create(user=user, org=org, role=role_group)
        else:
            return tokens.first()

    @classmethod
    def get_default_role(cls, org, user):
        """
        Gets the default API role for the given user
        """
        role = org.get_user_role(user)

        if not role or role.group.name not in cls.GROUP_GRANTED_TO:  # don't allow creating tokens for VIEWER role etc
            return None

        return role

    def is_valid(self) -> bool:
        """
        A user's role in an org can change so this return whether this token is still valid.
        """
        role = self.org.get_user_role(self.user)
        roles_allowed_this_perm_group = self.GROUP_GRANTED_TO.get(self.role.name, ())
        return role and role in roles_allowed_this_perm_group

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = self.generate_key()
        return super().save(*args, **kwargs)

    def generate_key(self):
        unique = uuid4()
        return hmac.new(unique.bytes, digestmod=sha1).hexdigest()

    def release(self):
        self.is_active = False
        self.save(update_fields=("is_active",))

    def __str__(self):
        return self.key
