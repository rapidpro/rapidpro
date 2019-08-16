import time

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from temba.policies.models import Policy
from temba.utils import analytics


class Command(BaseCommand):  # pragma: no cover
    help = "Updates consent status for each user"

    def handle(self, *args, **options):
        analytics.init_analytics()
        count = 0
        consented = 0

        # now all users
        users = User.objects.all().order_by("id")
        total = users.count()
        for user in users:

            # update their policy consent
            if Policy.get_policies_needing_consent(user):
                analytics.change_consent(user.email, False)
            else:
                consented += 1
                analytics.change_consent(user.email, True)

            time.sleep(0.1)
            count += 1
            if count % 1000 == 0:
                print(f"Updated {count} of {total} users")

        print(f"Updated {count} users ({consented} consented).")
