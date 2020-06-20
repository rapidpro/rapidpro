from unittest.mock import patch

from django.contrib.auth.models import User
from django.utils import timezone

from temba.contacts.models import Contact


def local_contact_modify(org_id, user_id, contact_ids, modifiers):
    """
    A simplified version of calling mailroom's contact_modify endpoint for testing.
    Doesn't do dynamic group re-evaluation.
    """
    user = User.objects.get(id=user_id)
    contacts = Contact.objects.filter(org_id=org_id, id__in=contact_ids)

    for mod in modifiers:
        fields = dict()
        clear_groups = False
        if mod["type"] == "name":
            fields = dict(name=mod["name"])
        elif mod["type"] == "status":
            if mod["status"] == "blocked":
                fields = dict(is_blocked=True, is_stopped=False)
                clear_groups = True
            elif mod["status"] == "stopped":
                fields = dict(is_blocked=False, is_stopped=True)
                clear_groups = True
            else:
                fields = dict(is_blocked=False, is_stopped=False)

        contacts.update(modified_by=user, modified_on=timezone.now(), **fields)
        if clear_groups:
            for cid in contact_ids:
                Contact.objects.get(id=cid).clear_all_groups(user)


def mock_contact_modify(f):
    """
    Convenience decorator to make a test method use a mocked version of contact_modify
    """

    def wrapped(*args, **kwargs):
        with patch("temba.mailroom.client.MailroomClient.contact_modify") as cm:
            cm.side_effect = local_contact_modify
            return f(*args, **kwargs)

    return wrapped
