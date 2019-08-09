import time

from django.conf import settings
from django.http import HttpResponseRedirect
from django.urls import resolve, get_script_prefix

from temba.orgs.context_processors import user_group_perms_processor


class FormaxMixin(object):
    def derive_formax_sections(self, formax, context):  # pragma: needs cover
        return None

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        formax = Formax(self.request)
        self.derive_formax_sections(formax, context)

        if len(formax.sections) > 0:
            context["formax"] = formax
        return context


class Formax(object):
    def __init__(self, request):
        self.sections = []
        self.request = request
        context = user_group_perms_processor(self.request)
        self.org = context["user_org"]

    def resolve_prefixed_url(self, url):
        """
        django.urls.resolve(url) is not an exact mirror for django.urls.reverse(url) as the
        latter prefixes SCRIPT_NAME to the result, but the former does not strip the prefix
        since Django expects the uWSGI component to do that before Django processes the URL.
        The result is that anywhere code expects to do a resolve(reverse(url)) we will need
        to first strip out the unwanted prefix.
        :param str url: the result of django.urls.reverse(url)
        :return: result of django.urls.resolve(prefix_stripped_url).
        """
        prefix = get_script_prefix()
        if url and prefix and prefix != "/" and url.startswith(prefix):
            return resolve(url[len(prefix) :])
        else:
            return resolve(url)

    def add_section(self, name, url, icon, action="formax", button="Save", nobutton=False, dependents=None):
        resolver = self.resolve_prefixed_url(url)
        self.request.META["HTTP_X_FORMAX"] = 1
        self.request.META["HTTP_X_PJAX"] = 1

        start = time.time()

        open = self.request.GET.get("open", None)
        if open == name:  # pragma: needs cover
            action = "open"

        response = resolver.func(self.request, *resolver.args, **resolver.kwargs)

        # redirects don't do us any good
        if not isinstance(response, HttpResponseRedirect):
            self.sections.append(
                dict(
                    name=name,
                    url=url,
                    response=response.rendered_content,
                    icon=icon,
                    action=action,
                    button=button,
                    nobutton=nobutton,
                    dependents=dependents,
                )
            )

        if settings.DEBUG:
            print("%s took: %f" % (url, time.time() - start))
