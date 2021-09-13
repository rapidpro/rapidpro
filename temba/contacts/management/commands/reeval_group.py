import time

from django.core.management.base import BaseCommand, CommandError

from temba.contacts.models import ContactGroup
from temba.mailroom import queue_populate_dynamic_group


class Command(BaseCommand):
    help = "Re-evaluates a smart group"

    def add_arguments(self, parser):
        parser.add_argument("group_uuid", help="UUID of contact group to re-evaluate.")

    def handle(self, group_uuid: str, *args, **kwargs):
        group = ContactGroup.user_groups.filter(uuid=group_uuid).first()
        if not group:
            raise CommandError("no such group")
        if not group.is_dynamic:
            raise CommandError("group is not a smart group")

        self.stdout.write(
            f"Queueing re-evaluation for group {group.name} with query '{group.query}' "
            f"and {group.get_member_count()} members..."
        )

        # mark group as evaluating
        group.status = ContactGroup.STATUS_EVALUATING
        group.save(update_fields=("status",))

        queue_populate_dynamic_group(group)

        while True:
            time.sleep(2)

            group.refresh_from_db()
            if group.status == ContactGroup.STATUS_READY:
                break

            self.stdout.write(f"  > {group.get_member_count()} members...")

        self.stdout.write(f"Re-evaluation complete with {group.get_member_count()} members.")
