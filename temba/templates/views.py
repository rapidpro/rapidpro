from smartmin.views import SmartCreateView, SmartCRUDL, SmartListView

from temba.orgs.views import OrgPermsMixin

from .models import Template


class TemplateCRUDL(SmartCRUDL):
    model = Template
    actions = ("create", "list")

    class List(OrgPermsMixin, SmartListView):
        fields = ("slug",)

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return qs.filter(org=self.request.user.get_org(), is_active=True)

    class Create(OrgPermsMixin, SmartCreateView):
        fields = ("name", "message")

        def pre_save(self, obj):
            user = self.request.user
            obj.org = user.get_org()
            obj.slug = Template.make_slug(obj.name)
            obj.created_by = user
            obj.modified_by = user
            return obj
