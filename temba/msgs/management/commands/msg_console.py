import threading

import requests
from colorama import Fore, init as colorama_init
from requests.exceptions import ConnectionError

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from temba import mailroom
from temba.contacts.models import URN
from temba.orgs.models import Org
from temba.tests.integration import TestChannel

COURIER_URL = "http://localhost:8080"
DEFAULT_ORG = "1"
DEFAULT_URN = "tel:+250788123123"


def get_org(id_or_name):  # pragma: no cover
    """
    Gets an org by its id or name. If more than one org has the name, first org is returned
    """
    try:
        org_id = int(id_or_name)
        try:
            return Org.objects.get(pk=org_id)
        except Org.DoesNotExist:
            raise CommandError(f"No such org with id {org_id}")
    except ValueError:
        org = Org.objects.filter(name=id_or_name).first()
        if not org:
            raise CommandError(f"No such org with name '{id_or_name}'")
        return org


class Command(BaseCommand):  # pragma: no cover
    def add_arguments(self, parser):
        parser.add_argument(
            "--org",
            type=str,
            action="store",
            dest="org",
            default=DEFAULT_ORG,
            help="The id or name of the workspace to send messages to",
        )

        parser.add_argument(
            "--urn", type=str, action="store", dest="urn", default=DEFAULT_URN, help="The URN to send messages from"
        )

    def handle(self, *args, **options):
        colorama_init()
        org = get_org(options["org"])
        scheme, path, *rest = URN.to_parts(options["urn"])

        db = settings.DATABASES["default"]
        db_url = f"postgres://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}?sslmode=disable"
        redis_url = settings.CACHES["default"]["LOCATION"]

        try:
            print(
                f"✅ Mailroom version {mailroom.get_client().version()} running at️ {Fore.CYAN}{settings.MAILROOM_URL}{Fore.RESET}"
            )
        except ConnectionError:
            launch = f'mailroom -db="{db_url}" -redis={redis_url}'
            raise CommandError(f"Unable to connect to mailroom. Please launch it with...\n\n{launch}")

        try:
            requests.get(COURIER_URL)
            print(f"✅ Courier running at️ {Fore.CYAN}{COURIER_URL}{Fore.RESET}")
        except ConnectionError:
            launch = f'courier -db="{db_url}" -redis={redis_url} -spool-dir="."'
            raise CommandError(f"Unable to connect to courier. Please launch it with...\n\n{launch}")

        try:
            channel = TestChannel.create(
                org, org.administrators.first(), COURIER_URL, callback=self.response_callback, scheme=scheme
            )
            print(f"✅ Testing channel started at️ {Fore.CYAN}{channel.server.base_url}{Fore.RESET}")
        except Exception as e:
            raise CommandError(f"Unable to start test channel: {str(e)}")

        print(
            f"\nSending messages to {Fore.CYAN}{org.name}{Fore.RESET} as {Fore.CYAN}{scheme}:{path}{Fore.RESET}. Use Ctrl+C to quit."
        )

        self.responses_wait = None
        try:
            while True:
                line = input(f"📱 {Fore.CYAN}{path}{Fore.RESET}> ")
                if not line:
                    continue

                msg_in = channel.incoming(path, line)

                # we wait up to 2 seconds for a response from courier
                self.responses_wait = threading.Event()
                self.responses_wait.wait(timeout=2)

                for response in org.msgs.filter(direction="O", id__gt=msg_in.id).order_by("id"):
                    print(f"💬 {Fore.GREEN}{response.channel.address}{Fore.RESET}> {response.text}")

        except KeyboardInterrupt:
            pass

    def response_callback(self, data):
        self.responses_wait.set()
