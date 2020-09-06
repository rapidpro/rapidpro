from gettext import gettext as _

from smartmin.views import SmartCreateView, SmartCRUDL, SmartFormView, SmartListView, SmartReadView, SmartUpdateView

from django import forms
from django.db.models import Max
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone

from temba.orgs.views import OrgPermsMixin
from temba.utils import analytics
from temba.utils.views import ComponentFormMixin

from .models import Consent, Policy


class PolicyCRUDL(SmartCRUDL):
    actions = ("create", "read", "update", "list", "admin", "history", "give_consent")
    model = Policy
    permissions = True

    class Admin(SmartListView):
        ordering = ("-created_on",)
        link_fields = ("policy_type",)
        title = "Policies"
        paginate_by = 500

        def get_gear_links(self):
            links = [dict(title=_("New Policy"), href=reverse("policies.policy_create"),)]
            return links

        def get_queryset(self, **kwargs):
            queryset = super().get_queryset(**kwargs)
            return queryset.filter(is_active=False)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["active_policies"] = Policy.objects.filter(is_active=True).order_by(*self.ordering)
            context["gear_links"] = self.get_gear_links()
            return context

    class Update(ComponentFormMixin, SmartUpdateView):
        pass

    class Create(ComponentFormMixin, SmartCreateView):

        # make sure we only have one active policy at a time
        def post_save(self, obj):
            Policy.objects.filter(policy_type=obj.policy_type, is_active=True).exclude(id=obj.id).update(
                is_active=False
            )
            return obj

    class History(SmartReadView):
        def derive_title(self):
            return self.get_object().get_policy_type_display()

    class Read(History):
        permission = None

        @classmethod
        def derive_url_pattern(cls, path, action):
            archive_types = (choice[0] for choice in Policy.TYPE_CHOICES)
            return r"^%s/(%s)/$" % (path, "|".join(archive_types))

        def get_requested_policy_type(self):
            return self.request.path.split("/")[-2]

        def get_object(self):
            policy_type = self.get_requested_policy_type()
            return Policy.objects.filter(policy_type=policy_type, is_active=True).order_by("-created_on").first()

    class List(SmartListView):
        title = _("Your Privacy")
        permission = None
        link_fields = ()

        def get_queryset(self, **kwargs):
            queryset = super().get_queryset(**kwargs)
            return queryset.filter(is_active=True).order_by("requires_consent", "-policy_type")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            user = self.request.user

            if not user.is_anonymous:
                needs_consent = Policy.get_policies_needing_consent(user)
                context["needs_consent"] = needs_consent

                if not needs_consent:
                    context["consent_date"] = Consent.objects.filter(user=user, revoked_on=None).aggregate(
                        Max("created_on")
                    )["created_on__max"]

            context["next"] = self.request.GET.get("next", None)

            return context

    class GiveConsent(OrgPermsMixin, SmartFormView):
        class ConsentForm(forms.ModelForm):
            consent = forms.BooleanField(required=False)

            class Meta:
                model = Consent
                fields = ("consent",)

        form_class = ConsentForm

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/consent/$" % path

        def form_valid(self, form):
            if form.cleaned_data["consent"]:

                analytics.change_consent(self.request.user.email, True)

                for policy in Policy.get_policies_needing_consent(self.request.user):
                    Consent.objects.create(policy=policy, user=self.request.user)
            else:

                # only revoke consent for currently active policies
                active_policies = Policy.objects.filter(is_active=True, requires_consent=True).values_list(
                    "id", flat=True
                )
                consents = Consent.objects.filter(
                    user=self.request.user, policy__id__in=active_policies, revoked_on=None
                )
                consents.update(revoked_on=timezone.now())
                # forget we were ever friends
                analytics.change_consent(self.request.user.email, False)

            redirect = self.request.POST.get("next")
            if not redirect:
                redirect = reverse("policies.policy_list")

            return HttpResponseRedirect(redirect)
