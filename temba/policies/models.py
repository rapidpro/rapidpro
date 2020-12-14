from gettext import gettext as _

import markdown
from smartmin.models import SmartModel

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from django.utils.safestring import mark_safe


class Policy(SmartModel):
    TYPE_PRIVACY = "privacy"
    TYPE_TOS = "tos"
    TYPE_COOKIE = "cookie"
    TYPE_CONTENT = "content"

    TYPE_CHOICES = (
        (TYPE_PRIVACY, _("Privacy Policy")),
        (TYPE_CONTENT, _("Content Guidelines")),
        (TYPE_TOS, _("Terms of Service")),
        (TYPE_COOKIE, _("Cookie Policy")),
    )

    policy_type = models.CharField(choices=TYPE_CHOICES, max_length=16, help_text=_("Choose the type of policy"))

    body = models.TextField(help_text=_("Enter the content of the policy (Markdown permitted)"))

    summary = models.TextField(null=True, blank=True, help_text=_("Summary of policy changes (Markdown permitted)"))

    requires_consent = models.BooleanField(default=True, help_text=_("Is Consent Required?"))

    def get_rendered_body(self):
        return mark_safe(markdown.markdown(self.body))

    def get_rendered_summary(self):
        return mark_safe(markdown.markdown(self.summary))

    def has_consent(self, user):
        return Consent.objects.filter(user=user, policy=self, revoked_on=None).first()

    @classmethod
    def get_policies_needing_consent(cls, user):
        consented = Consent.objects.filter(user=user, revoked_on=None).values_list("policy_id", flat=True)
        return Policy.objects.filter(requires_consent=True).exclude(id__in=consented)


class Consent(models.Model):

    user = models.ForeignKey(User, on_delete=models.PROTECT, help_text="The user consenting to this policy")

    policy = models.ForeignKey(
        Policy, on_delete=models.PROTECT, help_text="The policy the user is consenting to", related_name="policies"
    )

    revoked_on = models.DateTimeField(null=True, default=None, help_text="When this consent was revoked")

    created_on = models.DateTimeField(
        default=timezone.now, editable=False, blank=True, help_text="When consent was given by clicking on web form"
    )
