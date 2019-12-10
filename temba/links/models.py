import time

from itertools import chain

from django.db import models
from django.utils.translation import ugettext_lazy as _
from django.urls import reverse
from django.conf import settings

from smartmin.models import SmartModel

from temba.assets.models import register_asset_store
from temba.contacts.models import Contact
from temba.contacts.search import SearchException
from temba.orgs.models import Org
from temba.utils import chunk_list
from temba.utils.dates import datetime_to_str
from temba.utils.models import TembaModel
from temba.utils.export import BaseExportAssetStore, BaseExportTask, TableExporter
from temba.utils.text import clean_string


MAX_HISTORY = 50


class LinkException(Exception):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class Link(TembaModel):

    name = models.CharField(max_length=64, help_text=_("The name for this trackable link"))

    destination = models.URLField(max_length=255, help_text="The destination URL for this trackable link")

    org = models.ForeignKey(Org, related_name="links", on_delete=models.CASCADE)

    is_archived = models.BooleanField(default=False, help_text=_("Whether this trackable link is archived"))

    clicks_count = models.PositiveIntegerField(default=0, help_text="Clicks count for this trackable link")

    @classmethod
    def create(cls, org, user, name, destination):
        links_arg = dict(org=org, name=name, destination=destination, created_by=user, modified_by=user)
        link = Link.objects.create(**links_arg)
        return link

    def as_select2(self):
        return dict(text=self.name, id=self.uuid)

    def as_json(self):
        return dict(uuid=self.uuid, name=self.name, destination=self.destination)

    def get_permalink(self):
        return reverse("links.link_handler", args=[self.uuid])

    def get_url(self):
        protocol = "http" if settings.DEBUG else "https"
        return f"{protocol}://{settings.HOSTNAME}{self.get_permalink()}"

    @classmethod
    def apply_action_archive(cls, user, links):
        changed = []

        for link in links:
            link.archive()
            changed.append(link.pk)

        return changed

    @classmethod
    def apply_action_restore(cls, user, links):
        changed = []
        for link in links:
            try:
                link.restore()
                changed.append(link.pk)
            except LinkException:  # pragma: no cover
                pass
        return changed

    def archive(self):
        self.is_archived = True
        self.save(update_fields=["is_archived"])

    def restore(self):
        self.is_archived = False
        self.save(update_fields=["is_archived"])

    def get_activity(self, after, before, search):
        """
        Gets this link's activity of contacts in the given time window
        """

        contacts = LinkContacts.objects.filter(link=self, created_on__gte=after, created_on__lt=before)
        if search:
            try:
                contacts = Contact.objects.filter(
                    models.Q(name__contains=search), id__in=contacts.values_list("contact__id")
                ).only("id")
            except SearchException as e:
                self.search_error = str(e.message)
                contacts = Contact.objects.none()

        # wrap items, chain and sort by time
        activity = chain([{"type": "contact", "time": c.created_on, "obj": c} for c in contacts])

        return sorted(activity, key=lambda i: i["time"], reverse=True)[:MAX_HISTORY]

    def __str__(self):
        return self.name

    class Meta:
        ordering = ("-created_on",)


class LinkContacts(SmartModel):
    link = models.ForeignKey(Link, related_name="contacts", on_delete=models.CASCADE)

    contact = models.ForeignKey(
        Contact,
        related_name="contact_links",
        help_text=_("The users which clicked on this link"),
        on_delete=models.CASCADE,
    )

    def __str__(self):
        return f"{self.contact.get_display()}"


class ExportLinksTask(BaseExportTask):
    analytics_key = "link_export"
    email_subject = "Your trackable link export is ready"
    email_template = "links/email/links_export_download"

    link = models.ForeignKey(
        Link, null=True, related_name="exports", help_text=_("The trackable link to export"), on_delete=models.CASCADE
    )

    @classmethod
    def create(cls, org, user, link):
        return cls.objects.create(org=org, link=link, created_by=user, modified_by=user)

    def get_export_fields_and_schemes(self):

        fields = [
            dict(label="Contact UUID", key=Contact.UUID, id=0, field=None, urn_scheme=None),
            dict(label="Name", key=Contact.NAME, id=0, field=None, urn_scheme=None),
            dict(label="Date", key="date", id=0, field=None, urn_scheme=None),
            dict(label="Destination Link", key="destination", id=0, field=None, urn_scheme=None),
        ]

        # anon orgs also get an ID column that is just the PK
        if self.org.is_anon:
            fields = [dict(label="ID", key=Contact.ID, id=0, field=None, urn_scheme=None)] + fields

        return fields, dict()

    def write_export(self):
        fields, scheme_counts = self.get_export_fields_and_schemes()

        contact_ids = (
            self.link.contacts.filter(contact__is_test=False)
            .order_by("contact__name", "contact__id")
            .values_list("id", flat=True)
        )

        # create our exporter
        exporter = TableExporter(self, "Links", [f["label"] for f in fields])

        current_contact = 0
        start = time.time()

        # write out contacts in batches to limit memory usage
        for batch_ids in chunk_list(contact_ids, 1000):
            # fetch all the contacts for our batch
            batch_contacts = LinkContacts.objects.filter(id__in=batch_ids)

            # to maintain our sort, we need to lookup by id, create a map of our id->contact to aid in that
            contact_by_id = {c.id: c for c in batch_contacts}

            for contact_id in batch_ids:
                contact = contact_by_id[contact_id]

                values = []
                for col in range(len(fields)):
                    field = fields[col]

                    if field["key"] == Contact.NAME:
                        field_value = contact.contact.get_display()
                    elif field["key"] == Contact.UUID:
                        field_value = contact.contact.uuid
                    elif field["key"] == "date":
                        field_value = datetime_to_str(
                            contact.created_on, format="%m-%d-%Y %H:%M:%S", tz=self.link.org.timezone
                        )
                    elif field["key"] == "destination":
                        field_value = contact.link.destination
                    else:
                        field_value = contact.contact.get_field_display(field["field"])

                    if field_value is None:
                        field_value = ""

                    if field_value:
                        field_value = clean_string(field_value)

                    values.append(field_value)

                # write this contact's values
                exporter.write_row(values)
                current_contact += 1

                # output some status information every 10,000 contacts
                if current_contact % 10000 == 0:  # pragma: no cover
                    elapsed = time.time() - start
                    predicted = int(elapsed / (current_contact / (len(contact_ids) * 1.0)))

                    print(
                        "Export of %s contacts - %d%% (%s/%s) complete in %0.2fs (predicted %0.0fs)"
                        % (
                            self.org.name,
                            current_contact * 100 / len(contact_ids),
                            "{:,}".format(current_contact),
                            "{:,}".format(len(contact_ids)),
                            time.time() - start,
                            predicted,
                        )
                    )

        return exporter.save_file()


@register_asset_store
class ContactExportAssetStore(BaseExportAssetStore):
    model = ExportLinksTask
    key = "link_export"
    directory = "link_exports"
    permission = "links.link_export"
    extensions = ("xlsx", "csv")
