from __future__ import unicode_literals

"""
Our dashboards typically use HamlPy (https://github.com/jessemiller/HamlPy) for templates, but we need our own custom
template loaders for two reasons:

  1. That library is not being actively maintained and the included loaders are not compatible with Django 1.9
  2. We allow templates to be overridden even when the extension doesn't match, i.e. a template called index.haml can
     override index.html in Smartmin

"""

import os

from django.template import TemplateDoesNotExist
from django.template.base import Origin
from django.template.loaders import filesystem, app_directories

from hamlpy import hamlpy
from hamlpy.template.utils import get_django_template_loaders


def get_haml_loader(loader):
    if hasattr(loader, 'Loader'):
        baseclass = loader.Loader
    else:
        class baseclass(object):
            def load_template_source(self, *args, **kwargs):
                return loader.load_template_source(*args, **kwargs)

    class Loader(baseclass):
        def load_template_source(self, template_name, *args, **kwargs):
            """
            Used by Django 1.7, 1.8
            """
            _name, _extension = os.path.splitext(template_name)

            for extension in hamlpy.VALID_EXTENSIONS:
                try:
                    haml_source, template_path = super(Loader, self).load_template_source(
                        self._generate_template_name(_name, extension), *args, **kwargs
                    )
                except TemplateDoesNotExist:
                    pass
                else:
                    haml_parser = hamlpy.Compiler()
                    html = haml_parser.process(haml_source)

                    return html, template_path

            raise TemplateDoesNotExist(template_name)

        load_template_source.is_usable = True

        def get_contents(self, origin):
            """
            Used by Django 1.9+
            """
            name, _extension = os.path.splitext(origin.name)
            template_name, _extension = os.path.splitext(origin.template_name)

            for extension in hamlpy.VALID_EXTENSIONS:
                try_name = self._generate_template_name(name, extension)
                try_template_name = self._generate_template_name(template_name, extension)
                try_origin = Origin(try_name, try_template_name, origin.loader)
                try:
                    haml_source = super(Loader, self).get_contents(try_origin)
                except TemplateDoesNotExist:
                    pass
                else:
                    haml_parser = hamlpy.Compiler()
                    return haml_parser.process(haml_source)

            raise TemplateDoesNotExist(origin.template_name)

        def _generate_template_name(self, name, extension="hamlpy"):
            return "%s.%s" % (name, extension)

    return Loader


haml_loaders = dict((name, get_haml_loader(loader)) for (name, loader) in get_django_template_loaders())


HamlFilesystemLoader = get_haml_loader(filesystem)
HamlAppDirectoriesLoader = get_haml_loader(app_directories)
