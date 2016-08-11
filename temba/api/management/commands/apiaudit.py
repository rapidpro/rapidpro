from __future__ import unicode_literals

import time

from colorama import init as colorama_init, Fore, Style
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.core.urlresolvers import reverse
from rest_framework.test import APIClient

from temba.api.models import APIToken


ENDPOINT_TESTS = [
    # view name, querystring, expected index to use

    # API v1
    # ('api.v1.contacts', '', 'contacts_contact'),
    # ('api.v1.messages', '', 'msgs_msg'),

    # API v2
    ('api.v2.broadcasts', '', 'msgs_broadcast'),
    ('api.v2.channels', '', 'channels_channel'),
    ('api.v2.channel_events', '', 'channels_channelevent'),
    ('api.v2.contacts', '', 'contacts_contact'),
    ('api.v2.contacts', '?deleted=true', 'contacts_contact'),
    ('api.v2.fields', '', 'contacts_contactfield'),
    ('api.v2.groups', '', 'contacts_contactgroup'),
    ('api.v2.labels', '', 'msgs_label'),
    ('api.v2.messages', '?folder=incoming', 'msgs_msg'),
    ('api.v2.messages', '?folder=inbox', 'msgs_msg'),
    ('api.v2.messages', '?folder=flows', 'msgs_msg'),
    ('api.v2.messages', '?folder=archived', 'msgs_msg'),
    ('api.v2.messages', '?folder=outbox', 'msgs_msg'),
    ('api.v2.messages', '?folder=sent', 'msgs_msg'),
    ('api.v2.org', '', 'orgs_org'),
]

MAX_REQUEST_TIME = 1  # maximum number of seconds considered acceptable for a request


class Command(BaseCommand):  # pragma: no cover
    help = "Checks access and index usage of API endpoints"

    def add_arguments(self, parser):
        parser.add_argument(type=str, action='store', dest='token', metavar="APITOKEN",
                            help="The API token to test against")

    def handle(self, token, *args, **options):
        colorama_init()

        try:
            token_obj = APIToken.objects.get(key=token)
        except APIToken.DoesNotExist:
            raise CommandError("No such API token exists")

        user, org = token_obj.user, token_obj.org

        self.stdout.write("Checking with token %s for user %s [%d] in org %s [%d] with role %s...\n\n"
                          % (colored(token, Fore.BLUE),
                             colored(user.username, Fore.BLUE),
                             user.pk,
                             colored(org.name, Fore.BLUE),
                             org.pk,
                             colored(token_obj.role, Fore.BLUE)))

        for test in ENDPOINT_TESTS:
            self.test_url(token, *test)

    def test_url(self, token, view_name, query, table):
        url = reverse(view_name) + '.json' + query

        pre_index_scans = self.get_index_scan_counts(table)

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Token ' + token)

        start_time = time.time()
        response = client.get(url)
        time_taken = time.time() - start_time

        # TODO figure out how to get stats to refresh without this workaround.
        # You'd think that SELECT pg_stat_clear_snapshot() might work but no.
        connection.close()
        time.sleep(1)

        post_index_scans = self.get_index_scan_counts(table)

        access_result = colored(response.status_code, Fore.GREEN if 200 <= response.status_code < 300 else Fore.RED)

        time_result = colored("%.3f" % time_taken, Fore.GREEN if time_taken < MAX_REQUEST_TIME else Fore.RED) + " secs"

        used_indexes = [i for i, scans in pre_index_scans.iteritems() if post_index_scans.get(i) > scans]
        index_result = ",".join(used_indexes)

        self.stdout.write("GET %s %s / %s / %s" % (url, access_result, time_result, index_result))

    @staticmethod
    def get_index_scan_counts(table_name):
        cursor = connection.cursor()
        cursor.execute("SELECT indexrelname, idx_scan FROM pg_stat_user_indexes "
                       "WHERE schemaname = 'public' AND relname = %s", [table_name])
        rows = cursor.fetchall()
        if not rows:
            raise ValueError("No indexes for table %s. Wrong table name?" % table_name)

        return {row[0]: row[1] for row in rows}


def colored(text, color):
    return color + unicode(text) + Fore.RESET


def styled(text, style):
    return style + unicode(text) + Style.RESET_ALL
