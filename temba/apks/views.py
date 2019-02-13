from smartmin.views import SmartCreateView, SmartCRUDL, SmartListView, SmartReadView

from .models import Apk


class ApkCRUDL(SmartCRUDL):
    model = Apk
    permissions = True
    actions = ("create", "read", "update", "list")

    class Create(SmartCreateView):
        fields = ("apk_type", "name", "apk_file", "description")

    class List(SmartListView):
        fields = ("name", "apk_type", "apk_file", "description")

    class Read(SmartReadView):
        fields = ("name", "apk_type", "apk_file", "description")

        def get_apk_type(self, obj):
            return obj.get_apk_type_display()
