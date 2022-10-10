from smartmin.views import SmartCRUDL, SmartFormView, SmartReadView, SmartTemplateView, SmartUpdateView

from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from temba.orgs.views import DependencyDeleteModal, MenuMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.utils.views import ComponentFormMixin, ContentMenuMixin, SpaMixin

from .models import Classifier


class BaseConnectView(SpaMixin, ComponentFormMixin, OrgPermsMixin, SmartFormView):
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
            org = self.request.org

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

    class Read(SpaMixin, OrgObjPermsMixin, ContentMenuMixin, SmartReadView):
        slug_url_kwarg = "uuid"
        exclude = ("id", "is_active", "created_by", "modified_by", "modified_on")

        def build_content_menu(self, menu):
            obj = self.get_object()

            menu.add_link(_("Log"), reverse("request_logs.httplog_classifier", args=[obj.uuid]))

            if self.has_org_perm("classifiers.classifier_sync"):
                menu.add_url_post(_("Sync"), reverse("classifiers.classifier_sync", args=[obj.id]))

            if self.has_org_perm("classifiers.classifier_delete"):
                menu.add_modax(
                    _("Delete"),
                    "classifier-delete",
                    reverse("classifiers.classifier_delete", args=[obj.uuid]),
                    title=_("Delete Classifier"),
                )

        def get_queryset(self, **kwargs):
            queryset = super().get_queryset(**kwargs)
            return queryset.filter(org=self.request.org, is_active=True)

    class Sync(SpaMixin, OrgObjPermsMixin, SmartUpdateView):
        fields = ()
        success_url = "uuid@classifiers.classifier_read"
        success_message = ""
        title = _("Connect a Classifier")

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
            context["classifier_types"] = [t for t in Classifier.get_types()]
            return context
