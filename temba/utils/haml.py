# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

"""
We need our own custom template loaders because we allow templates to be overridden even when the extension doesn't
match, i.e. a template called index.haml can override index.html in Smartmin
"""

import os

from django.template import TemplateDoesNotExist
from django.template.base import Origin
from django.template.loaders import filesystem, app_directories

from hamlpy import HAML_EXTENSIONS
from hamlpy.compiler import Compiler
from hamlpy.template.utils import get_django_template_loaders


def get_haml_loader(loader):
    baseclass = loader.Loader

    class Loader(baseclass):
        def get_contents(self, origin):
            """
            Used by Django 1.9+
            """
            name, _extension = os.path.splitext(origin.name)
            template_name, _extension = os.path.splitext(origin.template_name)

            for extension in HAML_EXTENSIONS:
                try_name = self._generate_template_name(name, extension)
                try_template_name = self._generate_template_name(template_name, extension)
                try_origin = Origin(try_name, try_template_name, origin.loader)
                try:
                    haml_source = super(Loader, self).get_contents(try_origin)
                except TemplateDoesNotExist:
                    pass
                else:
                    haml_parser = Compiler()
                    return haml_parser.process(haml_source)

            raise TemplateDoesNotExist(origin.template_name)

        def _generate_template_name(self, name, extension="hamlpy"):
            return "%s.%s" % (name, extension)

    return Loader


haml_loaders = dict((name, get_haml_loader(loader)) for (name, loader) in get_django_template_loaders())


HamlFilesystemLoader = get_haml_loader(filesystem)
HamlAppDirectoriesLoader = get_haml_loader(app_directories)
