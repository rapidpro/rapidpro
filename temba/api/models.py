import hmac
import logging
from datetime import timedelta
from hashlib import sha1

from rest_framework.permissions import BasePermission
from smartmin.models import SmartModel

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba.orgs.models import Org
from temba.utils.cache import get_cacheable_attr
from temba.utils.models import JSONAsTextField
from temba.utils.uuid import uuid4

logger = logging.getLogger(__name__)


class APIPermission(BasePermission):
    """
    Verifies that the user has the permission set on the endpoint view
    """

    def has_permission(self, request, view):

        if getattr(view, "permission", None):
            # no anon access to API endpoints
            if request.user.is_anonymous:
                return False

            org = request.user.get_org()

            if request.auth:
                role_group = request.auth.role
                allowed_roles = APIToken.get_allowed_roles(org, request.user)

                # check that user is still allowed to use the token's role
                if role_group not in allowed_roles:
                    return False
            elif org:
                # user may not have used token authentication
                role_group = org.get_user_org_group(request.user)
            else:
                return False

            codename = view.permission.split(".")[-1]
            has_perm = role_group.permissions.filter(codename=codename).exists()

            # viewers can only ever get from the API
            if role_group.name == "Viewers":
                return has_perm and request.method == "GET"

            return has_perm

        else:  # pragma: no cover
            return True


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

    def as_select2(self):
        return dict(text=self.slug, id=self.slug)

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

    def release(self):
        self.delete()


class WebHookResult(models.Model):
    """
    Represents the result of trying to make a webhook call in a flow
    """

    # the org this result belongs to
    org = models.ForeignKey("orgs.Org", on_delete=models.PROTECT, related_name="webhook_results")

    # the url this result is for
    url = models.TextField(null=True, blank=True)

    # the body of the request
    request = models.TextField(null=True, blank=True)

    # the status code returned (set to 503 for connection errors)
    status_code = models.IntegerField()

    # the body of the response
    response = models.TextField(null=True, blank=True)

    # how long the request took to return in milliseconds
    request_time = models.IntegerField(null=True)

    # the contact associated with this result (if any)
    contact = models.ForeignKey(
        "contacts.Contact", on_delete=models.PROTECT, null=True, related_name="webhook_results"
    )

    # when this result was created
    created_on = models.DateTimeField(default=timezone.now, editable=False, blank=True)

    @classmethod
    def get_recent_errored(cls, org):
        past_hour = timezone.now() - timedelta(hours=1)
        return cls.objects.filter(org=org, status_code__gte=400, created_on__gte=past_hour)

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def release(self):
        self.delete()


class APIToken(models.Model):
    """
    Our API token, ties in orgs
    """

    CODE_TO_ROLE = {"A": "Administrators", "E": "Editors", "S": "Surveyors"}

    ROLE_GRANTED_TO = {
        "Administrators": ("Administrators",),
        "Editors": ("Administrators", "Editors"),
        "Surveyors": ("Administrators", "Editors", "Surveyors"),
        "Prometheus": ("Administrators",),
    }

    is_active = models.BooleanField(default=True)

    key = models.CharField(max_length=40, primary_key=True)

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="api_tokens")

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="api_tokens")

    created = models.DateTimeField(auto_now_add=True)

    role = models.ForeignKey(Group, on_delete=models.PROTECT)

    @classmethod
    def get_or_create(cls, org, user, role=None, refresh=False):
        """
        Gets or creates an API token for this user
        """
        if not role:
            role = cls.get_default_role(org, user)

        if not role:
            raise ValueError("User '%s' has no suitable role for API usage" % str(user))
        elif role.name not in cls.ROLE_GRANTED_TO:
            raise ValueError("Role %s is not valid for API usage" % role.name)

        tokens = cls.objects.filter(is_active=True, user=user, org=org, role=role)

        # if we are refreshing the token, clear existing ones
        if refresh and tokens:
            for token in tokens:
                token.release()
            tokens = None

        if not tokens:
            token = cls.objects.create(user=user, org=org, role=role)
        else:
            token = tokens.first()

        return token

    @classmethod
    def get_orgs_for_role(cls, user, role):
        """
        Gets all the orgs the user can access the API with the given role
        """
        user_query = Q()
        for user_group in cls.ROLE_GRANTED_TO.get(role.name):
            user_query |= Q(**{user_group.lower(): user})

        return Org.objects.filter(user_query)

    @classmethod
    def get_default_role(cls, org, user):
        """
        Gets the default API role for the given user
        """
        group = org.get_user_org_group(user)

        if not group or group.name not in cls.ROLE_GRANTED_TO:  # don't allow creating tokens for Viewers group etc
            return None

        return group

    @classmethod
    def get_allowed_roles(cls, org, user):
        """
        Gets all of the allowed API roles for the given user
        """
        group = org.get_user_org_group(user)

        if group:
            role_names = []
            for role_name, granted_to in cls.ROLE_GRANTED_TO.items():
                if group.name in granted_to:
                    role_names.append(role_name)

            return Group.objects.filter(name__in=role_names)
        else:
            return []

    @classmethod
    def get_role_from_code(cls, code):
        role = cls.CODE_TO_ROLE.get(code)
        return Group.objects.get(name=role) if role else None

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = self.generate_key()
        return super().save(*args, **kwargs)

    def generate_key(self):
        unique = uuid4()
        return hmac.new(unique.bytes, digestmod=sha1).hexdigest()

    def release(self):
        self.is_active = False
        self.save()

    def __str__(self):
        return self.key


def get_or_create_api_token(user):
    """
    Gets or creates an API token for this user. If user doen't have access to the API, this returns None.
    """
    org = user.get_org()
    if not org:
        org = Org.get_org(user)

    if org:
        try:
            token = APIToken.get_or_create(org, user)
            return token.key
        except ValueError:
            pass

    return None


def api_token(user):
    """
    Cached property access to a user's lazily-created API token
    """
    return get_cacheable_attr(user, "__api_token", lambda: get_or_create_api_token(user))


User.api_token = property(api_token)
