import requests
from colorama import Fore, init as colorama_init
from requests.exceptions import ConnectionError

from django.core.management.base import BaseCommand, CommandError

from temba.contacts.models import URN
from temba.orgs.models import Org
from temba.tests.integration import Messenger

COURIER_URL = "http://localhost:8080"
DEFAULT_ORG = "1"
DEFAULT_URN = "tel:+250788123123"


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
        org = self.get_org(options["org"])
        user = org.get_admins().first()
        scheme, path, *rest = URN.to_parts(options["urn"])

        self.prompt = f"📱 {Fore.CYAN}{path}{Fore.RESET}> "

        try:
            requests.get(COURIER_URL)
            self.stdout.write(f"✅ Courier running at️ {Fore.CYAN}{COURIER_URL}{Fore.RESET}")
        except ConnectionError:
            raise CommandError(f"Unable to connect to courier at {COURIER_URL}")

        try:
            self.messenger = Messenger.create(org, user, COURIER_URL, callback=self.response_callback, scheme=scheme)
            self.stdout.write(f"✅ Messenger started at️ {Fore.CYAN}{self.messenger.server.base_url}{Fore.RESET}")
        except Exception as e:
            raise CommandError(f"Unable to start messenger: {str(e)}")

        self.stdout.write(
            f"\nSending messages to {Fore.CYAN}{org.name}{Fore.RESET} as {Fore.CYAN}{scheme}:{path}{Fore.RESET}. "
            "Use Ctrl+C to quit."
        )

        try:
            while True:
                line = input(self.prompt)
                if not line:
                    continue

                self.messenger.incoming(path, line)

        except KeyboardInterrupt:
            self.messenger.release(release_channel=False)
            self.stdout.write("🛑 Messenger stopped")

    def response_callback(self, data):
        print("\033[2K\033[1G", end="")  # erase current line and move cursor to start of line
        print(f"📠 {Fore.GREEN}{self.messenger.channel.address}{Fore.RESET}> {data['text']}")
        print(self.prompt, end="", flush=True)

    def get_org(self, id_or_name):
        """
        Gets an org by its id or name. If more than one org has the name, first org is returned
        """
        try:
            org_id = int(id_or_name)
            try:
                return Org.objects.get(id=org_id)
            except Org.DoesNotExist:
                raise CommandError(f"No such org with id {org_id}")
        except ValueError:
            org = Org.objects.filter(name=id_or_name).first()
            if not org:
                raise CommandError(f"No such org with name '{id_or_name}'")
            return org
