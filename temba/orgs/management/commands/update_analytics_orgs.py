import time

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db.models import Q

from temba.orgs.models import Org
from temba.utils import analytics


class Command(BaseCommand):  # pragma: no cover
    help = "Updates org membership"

    def handle(self, *args, **options):
        analytics.init_analytics()
        count = 0

        users = User.objects.all().order_by("id")
        total = users.count()
        for user in users:

            # update their orgs
            users = (user,)
            orgs = Org.objects.filter(
                Q(administrators__in=users) | Q(editors__in=users) | Q(viewers__in=users), is_active=True
            )

            analytics.set_orgs(user.email, orgs)
            time.sleep(0.1)
            count += 1
            if count % 1000 == 0:
                print(f"Updated {count} of {total} users")

        print(f"Updated {count} users.")
