from __future__ import unicode_literals

from functools import wraps
from django.utils.decorators import available_attrs

# This middleware allows us to short circuit middleware processing for views
# that are decorated with @disable_middleware.
#
# We use this for some of our API views, specifically ones coming in from aggregators
# where most of the middlewares are not useful to us.


def disable_middleware(view_func):
    def wrapped_view(*args, **kwargs):
        return view_func(*args, **kwargs)
    wrapped_view.disable_middleware = True
    return wraps(view_func, assigned=available_attrs(view_func))(wrapped_view)


class DisableMiddleware(object):
    """
    Middleware; looks for a view attribute 'disable_middleware'
    and short-circuits. Relies on the fact that if you return an HttpResponse
    from a view, it will short-circuit other middleware, see:
    https://docs.djangoproject.com/en/dev/topics/http/middleware/#process-request
    """
    def process_view(self, request, view_func, view_args, view_kwargs):
        if getattr(view_func, 'disable_middleware', False):
            return view_func(request, *view_args, **view_kwargs)
        return None
