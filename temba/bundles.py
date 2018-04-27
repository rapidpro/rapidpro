# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from decimal import Decimal


def get_brand_bundles(branding):
    bundles = branding.get('bundles')
    for b in bundles:
        b['description'] = "$%d - %d Credits" % (int(b['cents']) // 100, b['credits'])
        b['dollars'] = int(b['cents']) // 100
        b['per_credit'] = (Decimal(b['cents']) / Decimal(b['credits'])).quantize(Decimal(".1"))
    return bundles


def get_bundle_map(bundles):
    bundle_map = dict()
    for b in bundles:
        bundle_map[b['cents']] = b
    return bundle_map


# a map of our price in US cents vs the number of messages you purchase for
def get_bundle_choices(bundles):  # pragma: no cover
    return [(b['cents'], b['description']) for b in bundles]
