from __future__ import print_function, unicode_literals

from celery.task import task
from django_redis import get_redis_connection
from twython import Twython

from temba.contacts.models import ContactURN, URN, TWITTER_SCHEME
from temba.utils import chunk_list
from django.conf import settings


@task(track_started=True, name='resolve_twitter_ids_task')
def resolve_twitter_ids():
    r = get_redis_connection()

    # TODO: we can't use our non-overlapping task decorator as it creates a loop in the celery resolver when registering
    if r.get('resolve_twitter_ids_task'):  # pragma: no cover
        return

    with r.lock('resolve_twitter_ids_task', 900):
        # look up all twitter contact URNs without a display, limiting to 30k since that's the most our API would allow anyways
        twitter_urns = ContactURN.objects.filter(scheme=TWITTER_SCHEME, display=None)[:30000].values('org_id', 'contact_id', 'id', 'path')

        api_key = settings.TWITTER_API_KEY
        api_secret = settings.TWITTER_API_SECRET
        client = Twython(api_key, api_secret)

        updated = 0
        missing = 0

        print("found %d twitter urns to resolve" % len(twitter_urns))

        # we try to look these up 100 at a time
        for urn_batch in chunk_list(twitter_urns, 100):
            screen_names = [u['path'] for u in urn_batch]
            screen_map = {u['path']: u for u in urn_batch}

            # try to fetch our users
            try:
                resp = client.lookup_user(screen_name=",".join(screen_names))
                for twitter_user in resp:
                    screen_name = twitter_user['screen_name']
                    twitter_id = twitter_user['id']
                    new_identity = URN.normalize(URN.from_parts(TWITTER_SCHEME, twitter_id))

                    if screen_name in screen_map and twitter_user['id']:
                        contact_urn = screen_map[screen_name]

                        # check if this URN already exists
                        existing = ContactURN.objects.filter(org_id=contact_urn['org_id'], identity=new_identity).first()
                        if existing:
                            # this URN already exists and has a contact associated with it, remove ourselves
                            if existing.contact:
                                ContactURN.objects.filter(id=contact_urn['id']).delete()
                                continue

                            else:
                                # the URN already exists but isn't tied to a contact, remove it instead
                                existing.delete()

                        # update our URN
                        ContactURN.objects.filter(id=contact_urn['id']).update(path=twitter_id,
                                                                               display=screen_name,
                                                                               identity=new_identity)

                        del screen_map[screen_name]
                        updated += 1

                missing += len(screen_map)

            except Exception as e:  # pragma: no cover
                # exit, we'll try again later
                print("exiting resolve_twitter_ids due to exception: %s" % e)
                break

        if len(twitter_urns) > 0:
            print("updated %d twitter urns, %d missing" % (updated, missing))
