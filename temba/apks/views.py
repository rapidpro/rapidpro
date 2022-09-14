from smartmin.views import SmartCreateView, SmartCRUDL, SmartListView, SmartUpdateView

from temba.utils.views import StaffOnlyMixin

from .models import Apk


class ApkCRUDL(SmartCRUDL):
    model = Apk
    permissions = True
    actions = ("create", "update", "list")

    class Create(StaffOnlyMixin, SmartCreateView):
        fields = ("apk_type", "version", "pack", "apk_file", "description")

    class Update(StaffOnlyMixin, SmartUpdateView):
        pass

    class List(StaffOnlyMixin, SmartListView):
        fields = ("apk_type", "version", "pack", "apk_file", "created_on")

        def get_apk_type(self, obj):
            return obj.get_apk_type_display()
