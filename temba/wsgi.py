# -*- coding: utf-8 -*-
"""
WSGI config for RapidPro project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/dev/howto/deployment/wsgi/
"""

import os  # pragma: needs cover
from django.core.wsgi import get_wsgi_application  # pragma: needs cover

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "temba.settings")  # pragma: needs cover
application = get_wsgi_application()  # pragma: needs cover
