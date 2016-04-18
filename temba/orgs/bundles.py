from __future__ import unicode_literals

from decimal import Decimal

WELCOME_TOPUP_SIZE = 1000

BUNDLES = (dict(cents="2000", credits=1000),
           dict(cents="4000", credits=2500),
           dict(cents="14000", credits=10000),
           dict(cents="25000", credits=20000),
           dict(cents="55000", credits=50000),
           dict(cents="100000", credits=100000),
           dict(cents="225000", credits=250000),
           dict(cents="400000", credits=500000))

for b in BUNDLES:
    b['description'] = "$%d - %d Credits" % (int(b['cents']) / 100, b['credits'])
    b['dollars'] = int(b['cents']) / 100
    b['per_credit'] = per_credit = (Decimal(b['cents']) / Decimal(b['credits'])).quantize(Decimal(".1"))

BUNDLE_MAP = dict()
for b in BUNDLES:
    BUNDLE_MAP[b['cents']] = b

# a map of our price in US cents vs the number of messages you purchase for
# that price
BUNDLE_CHOICES = [(b['cents'], b['description']) for b in BUNDLES]
