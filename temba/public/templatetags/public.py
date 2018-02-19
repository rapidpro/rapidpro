# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django import template
from django.utils.safestring import mark_safe


register = template.Library()


@register.filter
def gear_link_classes(link, showStyle=False):
    classes = []
    if link.get('posterize', False):
        classes.append('posterize')

    if link.get('js_class', False):
        classes.append(link.get('js_class'))

    if link.get('style', None) and showStyle:
        classes.append(link.get('style'))

    if link.get('modal', False):
        classes.append('gear-modal')

    if link.get('delete', False):
        classes.append('gear-delete')

    return mark_safe(" ".join(classes))
