from django.core.management import BaseCommand
from django.utils.timesince import timesince

from temba.contacts.models import ExportContactsTask
from temba.flows.models import ExportFlowResultsTask
from temba.msgs.models import ExportMessagesTask


class Command(BaseCommand):
    help = "Runs pre-deployment checks"

    def handle(self, *args, **kwargs):
        unfinished_tasks = {
            "contact-export": ExportContactsTask.get_unfinished(),
            "result-export": ExportFlowResultsTask.get_unfinished(),
            "message-export": ExportMessagesTask.get_unfinished(),
        }

        for name, qs in unfinished_tasks.items():
            count = qs.count()
            if count:
                last = qs.order_by("created_on").last()

                self.stdout.write(
                    f"WARNING: there are {count} unfinished tasks of type {name}. "
                    f"Last one started {timesince(last.created_on)} ago."
                )
