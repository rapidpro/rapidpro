from smartmin.views import SmartCRUDL, SmartListView, SmartReadView

from temba.orgs.views import DependencyUsagesModal, OrgObjPermsMixin, OrgPermsMixin
from temba.utils.views import SpaMixin

from .models import Template, TemplateTranslation


class TemplateCRUDL(SmartCRUDL):
    model = Template
    actions = ("list", "read", "usages")

    class List(SpaMixin, OrgPermsMixin, SmartListView):
        default_order = ("-created_on",)

        def derive_menu_path(self):
            return "/msg/templates"

        def get_queryset(self, **kwargs):
            return Template.annotate_usage(super().get_queryset(**kwargs).filter(org=self.request.org))

    class Read(SpaMixin, OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"
        status_icons = {
            TemplateTranslation.STATUS_PENDING: "template_pending",
            TemplateTranslation.STATUS_APPROVED: "template_approved",
            TemplateTranslation.STATUS_REJECTED: "template_rejected",
            TemplateTranslation.STATUS_UNSUPPORTED: "template_unsupported",
        }

        def derive_menu_path(self):
            return "/msg/templates"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["translations"] = context["object"].translations.order_by("locale", "channel")
            context["status_icons"] = self.status_icons
            return context

    class Usages(DependencyUsagesModal):
        permission = "templates.template_read"
