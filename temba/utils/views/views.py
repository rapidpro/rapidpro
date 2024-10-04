import logging

from django.http import HttpResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)


class ExternalURLHandler(View):
    """
    It's useful to register Courier and Mailroom URLs in RapidPro so they can be used in templates, and if they are hit
    here, we can provide the user with a error message about
    """

    service = None

    @csrf_exempt
    def dispatch(self, request, *args, **kwargs):
        logger.error(f"URL intended for {self.service} reached RapidPro", extra={"URL": request.get_full_path()})
        return HttpResponse(f"this URL should be mapped to a {self.service} instance", status=404)


class CourierURLHandler(ExternalURLHandler):
    service = "Courier"


class MailroomURLHandler(ExternalURLHandler):
    service = "Mailroom"
