# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import cmd

from colorama import init as colorama_init, Fore, Style
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from temba.contacts.models import Contact, URN
from temba.orgs.models import Org
from temba.msgs.models import Msg, OUTGOING
import six


DEFAULT_ORG = '1'
DEFAULT_URN = "tel:+250788123123"


def get_org(id_or_name):
    """
    Gets an org by its id or name. If more than one org has the name, first org is returned
    """
    try:
        org_id = int(id_or_name)
        try:
            return Org.objects.get(pk=org_id)
        except Org.DoesNotExist:
            raise CommandError("No such org with id %d" % org_id)
    except ValueError:
        org = Org.objects.filter(name=id_or_name).first()
        if not org:
            raise CommandError("No such org with name '%s'" % id_or_name)
        return org


class MessageConsole(cmd.Cmd):
    """
    Useful REPL'like utility to simulate sending messages into RapidPro. Mostly useful for testing things
    with real contacts across multiple flows and contacts where the simulator isn't enough.
    """
    def __init__(self, org, urn, *args, **kwargs):
        cmd.Cmd.__init__(self, *args, **kwargs)

        self.org = org
        self.user = org.get_org_users()[0]
        self.user.set_org(org)

        self.urn = urn
        self.contact = self.get_or_create_contact(urn)

        self.update_prompt()
        self.echoed = []

    def clear_echoed(self):
        self.echoed = []

    def echo(self, line):
        print(line)
        self.echoed.append(line)

    def update_prompt(self):
        self.prompt = ("\n" + Fore.CYAN + "[%s] " + Fore.WHITE) % self.contact

    def get_or_create_contact(self, urn):
        if ':' not in urn:
            urn = URN.from_tel(urn)  # assume phone number

        contact, urn_obj = Contact.get_or_create(self.org, urn, user=self.user)
        return contact

    def do_org(self, line):
        """
        Changes the current org
        """
        if not line:
            self.echo("Select org with org id or name.  ex: org 4")

            # list all org options
            for org in Org.objects.all().order_by('pk'):
                user = ""
                if org.get_org_admins():
                    user = org.get_org_admins()[0].username
                self.echo((Fore.YELLOW + "[%d]" + Fore.WHITE + " %s % 40s") % (org.pk, org.name, user))
        else:
            try:
                self.org = get_org(line)
                self.user = self.org.get_org_admins()[0]
                self.user.set_org(self.org)
                self.contact = self.get_or_create_contact(self.urn)

                self.update_prompt()

                self.echo("You are now sending messages for %s [%d]" % (self.org.name, self.org.pk))
                self.echo("You are now sending as %s [%d]" % (self.contact, self.contact.pk))
            except Exception as e:
                self.echo("Error changing org: %s" % e)

    def do_contact(self, line):
        """
        Sets the current contact by URN
        """
        if not line:
            self.echo("Set contact by specifying URN, ex: tel:+250788123123")
        else:
            self.contact = self.get_or_create_contact(line)
            self.update_prompt()
            self.echo("You are now sending as %s [%d]" % (self.contact, self.contact.pk))

    def default(self, line):
        """
        Sends a message as the current contact's highest priority URN
        """
        urn = self.contact.get_urn()

        incoming = Msg.create_incoming(None, URN.from_parts(urn.scheme, urn.path),
                                       line, date=timezone.now(), org=self.org)

        self.echo((Fore.GREEN + "[%s] " + Fore.YELLOW + ">>" + Fore.MAGENTA + " %s" + Fore.WHITE) % (six.text_type(urn), incoming.text))

        # look up any message responses
        outgoing = Msg.objects.filter(org=self.org, pk__gt=incoming.pk, direction=OUTGOING).order_by('sent_on')
        for response in outgoing:
            self.echo((Fore.GREEN + "[%s] " + Fore.YELLOW + "<<" + Fore.MAGENTA + " %s" + Fore.WHITE) % (six.text_type(urn), response.text))

    def do_EOF(self, line):
        """
        Exit console
        """
        return True

    def do_exit(self, line):
        """
        Exit console
        """
        return True


class Command(BaseCommand):  # pragma: no cover

    def add_arguments(self, parser):
        parser.add_argument('--org', type=str, action='store', dest='org', default=DEFAULT_ORG,
                            help="The id or name of the organization to send messages for")

        parser.add_argument('--urn', type=str, action='store', dest='urn', default=DEFAULT_URN,
                            help="The URN of the contact to send messages for")

    def handle(self, *args, **options):
        colorama_init()

        org = get_org(options['org'])
        urn = options['urn']

        intro = Style.BRIGHT + "Welcome to the message console.\n\n"
        intro += Style.NORMAL + "Send messages by typing anything\n"
        intro += "Change org with the org command, ex: " + Fore.YELLOW + "org 3" + Fore.WHITE + "\n"
        intro += "Change contact with the contact command, ex: " + Fore.YELLOW + "contact tel:+250788124124" + Fore.WHITE + "\n"
        intro += "Exit with the " + Fore.YELLOW + "exit" + Fore.WHITE + " command\n\n"

        intro += ("Currently sending messages for %s [%d] as " + Fore.CYAN + "%s" + Fore.WHITE) % (org.name, org.id, urn)

        MessageConsole(org, urn).cmdloop(intro=intro)
