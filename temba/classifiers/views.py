from smartmin.views import SmartCRUDL, SmartFormView, SmartReadView, SmartTemplateView, SmartUpdateView

from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from temba.orgs.views import DependencyDeleteModal, MenuMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.utils.views import ComponentFormMixin, SpaMixin

from .models import Classifier


class BaseConnectView(ComponentFormMixin, OrgPermsMixin, SmartFormView):
    permission = "classifiers.classifier_connect"
    classifier_type = None

    def __init__(self, classifier_type):
        self.classifier_type = classifier_type
        super().__init__()

    def get_template_names(self):
        return (
            "classifiers/types/%s/connect.html" % self.classifier_type.slug,
            "classifiers/classifier_connect_form.html",
        )

    def derive_title(self):
        return _("Connect") + " " + self.classifier_type.name

    def get_success_url(self):
        return reverse("classifiers.classifier_read", args=[self.object.uuid])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_blurb"] = self.classifier_type.get_form_blurb()
        return context


class ClassifierCRUDL(SmartCRUDL):
    model = Classifier
    actions = ("read", "connect", "delete", "sync", "menu")

    class Menu(MenuMixin, OrgPermsMixin, SmartTemplateView):
        def derive_menu(self):
            org = self.request.user.get_org()

            menu = []
            if self.has_org_perm("classifiers.classifier_read"):
                classifiers = Classifier.objects.filter(org=org, is_active=True).order_by("-created_on")
                for classifier in classifiers:
                    menu.append(
                        self.create_menu_item(
                            menu_id=classifier.uuid,
                            name=classifier.name,
                            href=reverse("classifiers.classifier_read", args=[classifier.uuid]),
                            icon=classifier.get_type().icon.replace("icon-", ""),
                        )
                    )

            menu.append(
                {
                    "id": "connect",
                    "href": reverse("classifiers.classifier_connect"),
                    "name": _("Add Classifier"),
                }
            )

            return menu

    class Delete(DependencyDeleteModal):
        cancel_url = "uuid@classifiers.classifier_read"
        success_url = "@orgs.org_home"
        success_message = _("Your classifier has been deleted.")

    class Read(SpaMixin, OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"
        exclude = ("id", "is_active", "created_by", "modified_by", "modified_on")

        def get_gear_links(self):
            links = [dict(title=_("Log"), href=reverse("request_logs.httplog_classifier", args=[self.object.uuid]))]

            if self.has_org_perm("classifiers.classifier_sync"):
                links.append(
                    dict(
                        title=_("Sync"),
                        style="btn-secondary",
                        posterize=True,
                        href=reverse("classifiers.classifier_sync", args=[self.object.id]),
                    )
                )
            if self.has_org_perm("classifiers.classifier_delete"):
                links.append(
                    dict(
                        id="ticketer-delete",
                        title=_("Delete"),
                        modax=_("Delete Classifier"),
                        href=reverse("classifiers.classifier_delete", args=[self.object.uuid]),
                    )
                )

            return links

        def get_queryset(self, **kwargs):
            queryset = super().get_queryset(**kwargs)
            return queryset.filter(org=self.request.user.get_org(), is_active=True)

    class Sync(OrgObjPermsMixin, SmartUpdateView):
        fields = ()
        success_url = "uuid@classifiers.classifier_read"
        success_message = ""

        def post(self, *args, **kwargs):
            self.object = self.get_object()

            try:
                self.object.sync()
                messages.info(self.request, _("Your classifier has been synced."))
            except Exception:
                messages.error(self.request, _("Unable to sync classifier. See the log for details."))

            return HttpResponseRedirect(self.get_success_url())

    class Connect(SpaMixin, OrgPermsMixin, SmartTemplateView):
        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["classifier_types"] = Classifier.get_types()
            return context
