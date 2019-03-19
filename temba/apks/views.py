from smartmin.views import SmartCreateView, SmartCRUDL, SmartListView, SmartReadView

from django.http import HttpResponseRedirect

from .models import Apk


class ApkCRUDL(SmartCRUDL):
    model = Apk
    permissions = True
    actions = ("create", "update", "list", "download")

    class Create(SmartCreateView):
        fields = ("apk_type", "version", "pack", "apk_file", "description")

    class List(SmartListView):
        fields = ("apk_type", "version", "pack", "apk_file", "created_on")

        def get_apk_type(self, obj):
            return obj.get_apk_type_display()

    class Download(SmartReadView):
        permission = None

        def render_to_response(self, context, **response_kwargs):
            return HttpResponseRedirect(self.get_object().apk_file.url)
