from __future__ import unicode_literals

from djcelery_transactions import task
from temba.contacts.models import ContactURN
from temba.triggers.models import Trigger
from temba.utils.mage import mage_handle_new_contact
from temba.channels.models import Channel


@task(track_started=True, name='fire_follow_triggers')  # pragma: no cover
def fire_follow_triggers(channel_id, contact_urn_id, new_mage_contact=False):
    """
    Fires a follow trigger
    """
    urn = ContactURN.objects.select_related('contact').get(pk=contact_urn_id)
    contact = urn.contact  # for now, flows start against contacts rather than URNs
    channel = Channel.objects.get(id=channel_id)

    # if contact was just created in Mage then..
    # * its dynamic groups won't have been initialized
    # * we need to update our cached contact counts
    if new_mage_contact:
        mage_handle_new_contact(contact.org, contact)

    Trigger.catch_triggers(contact, Trigger.TYPE_FOLLOW, channel)
