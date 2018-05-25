import traceback
import time

from django.core.management.base import BaseCommand
from temba.orgs.models import Org
from temba.utils import analytics
from itertools import chain


class Command(BaseCommand):  # pragma: no cover
    help = "Iterates all users on active organizations, calling identify on each"

    def handle(self, *args, **options):
        analytics.init_analytics()

        count = 0
        for org in Org.objects.filter(is_active=True).order_by("id"):
            for u in chain(org.administrators.all(), org.editors.all(), org.viewers.all()):
                try:
                    analytics.identify(
                        u.email, f"{u.first_name} {u.last_name}", dict(org=org.name, org_id=org.id, brand=org.brand)
                    )
                    time.sleep(.1)
                    count += 1
                except Exception as e:
                    traceback.print_exc()

            print(f"Processed {org.name}")

        print(f"Added {count} users.")
