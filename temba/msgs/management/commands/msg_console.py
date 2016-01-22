from __future__ import unicode_literals

import cmd

from colorama import init, Fore, Style
from django.core.management.base import BaseCommand
from django.utils import timezone
from optparse import make_option
from temba.contacts.models import Contact, TEL_SCHEME
from temba.orgs.models import Org
from temba.msgs.models import Msg, OUTGOING

class MessageConsole(cmd.Cmd):
    """
    Useful REPL'like utility to simulate sending messages into RapidPro. Mostly useful for testing things
    with real contacts across multiple flows and contacts where the simulator isn't enough.
    """

    def __init__(self, org, *args, **kwargs):
        cmd.Cmd.__init__(self, *args, **kwargs)

        self.org = org
        self.user = org.get_org_users()[0]
        self.user.set_org(org)

        self.contact = self.get_contact("+250788123123")

        self.update_prompt()
        self.echoed = []

    def clear_echoed(self):
        self.echoed = []

    def echo(self, line):
        print(line)
        self.echoed.append(line)

    def update_prompt(self):
        urn = self.contact.get_urn()
        self.prompt = ("\n" + Fore.CYAN + "[%s] " + Fore.WHITE) % urn.path

    def get_contact(self, number):
        return Contact.get_or_create(self.org, self.user, name=None, urns=[(TEL_SCHEME, number)])

    def do_org(self, line):
        """
        Changes to the org with the specified id
        """
        if not line:
            self.echo("Select org with org id.  ex: org 4")

            # list all org options
            for org in Org.objects.all().order_by('pk'):
                user = ""
                if org.get_org_admins():
                    user = org.get_org_admins()[0].username
                self.echo((Fore.YELLOW + "[%d]" + Fore.WHITE + " %s % 40s") % (org.pk, org.name, user))
        else:
            try:
                self.org = Org.objects.get(pk=line)
                self.user = self.org.get_org_admins()[0]
                self.user.set_org(self.org)
                self.contact = self.get_contact(self.contact.get_urn().path)
                self.echo("You are now sending messages for %s" % self.org.name)
                self.echo("You are now sending as %s [%d]" % (self.contact, self.contact.pk))
            except Exception as e:
                self.echo("Error changing org: %s" % e)

    def do_contact(self, line):
        """
        Sets the current contact by URN
        """
        if not line:
            self.echo("Set contact by specifying URN.  ex: phone tel:+250788123123")
        else:
            self.contact = self.get_contact(line)
            self.update_prompt()
            self.echo("You are now sending as %s [%d]" % (self.contact, self.contact.pk))

    def default(self, line):
        """
        Sends a message as the current contact's highest priority URN
        """
        urn = self.contact.get_urn()

        incoming = Msg.create_incoming(None, (urn.scheme, urn.path), line, date=timezone.now(), org=self.org)

        self.echo((Fore.GREEN + "[%s] " + Fore.YELLOW + ">>" + Fore.MAGENTA + " %s" + Fore.WHITE) % (urn.urn, incoming.text))

        # look up any message responses
        outgoing = Msg.all_messages.filter(org=self.org, pk__gt=incoming.pk, direction=OUTGOING)
        for response in outgoing:
            self.echo((Fore.GREEN + "[%s] " + Fore.YELLOW + "<<" + Fore.MAGENTA + " %s" + Fore.WHITE) % (urn.urn, response.text))

    def do_EOF(self, line):
        """
        Exit console
        """
        return True


class Command(BaseCommand):  # pragma: no cover
    option_list = BaseCommand.option_list + (
        make_option('--org',
                    action='store',
                    dest='org',
                    default=1,
                    help='The id of the organization to send message for'),
    )

    def handle(self, *args, **options):
        org = Org.objects.get(pk=int(options['org']))
        init()
        print("Sending messages for %s\n" % org.name)

        intro = Style.BRIGHT + "Welcome to the message console." + \
                Style.NORMAL + "\n\nSend messages by typing anything." + \
                "\nChange org with the org command. ex: org 3" + \
                "\nChange contact with the contact command. ex: contact 250788124124"

        MessageConsole(org).cmdloop(intro=intro)
