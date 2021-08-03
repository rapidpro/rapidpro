from django.db import models
from django.db.models import Q
from django.utils.translation import ugettext_lazy as _

from django_redis import get_redis_connection

from smartmin.models import SmartModel

from temba.contacts.models import ContactField, ContactGroup
from temba.utils import json
from temba.utils.models import JSONField
from temba.orgs.models import Org


VALUE_SUMMARY_CACHE_KEY = "value_summary"
CONTACT_KEY = "vsd::vsc%s"
GROUP_KEY = "vsd::vsg%s"
RULESET_KEY = "vsd::vsr%s"

# cache for up to 30 days (we will invalidate manually when dependencies change)
VALUE_SUMMARY_CACHE_TIME = 60 * 60 * 24 * 30


class Report(SmartModel):
    TITLE = "title"
    DESCRIPTION = "description"
    CONFIG = "config"
    ID = "id"

    title = models.CharField(verbose_name=_("Title"), max_length=64, help_text=_("The name title or this report"))

    description = models.TextField(verbose_name=_("Description"), help_text=_("The full description for the report"))

    org = models.ForeignKey(Org, on_delete=models.PROTECT)

    config = JSONField(
        null=True, verbose_name=_("Configuration"), help_text=_("The JSON encoded configurations for this report")
    )

    is_published = models.BooleanField(default=False, help_text=_("Whether this report is currently published"))

    @classmethod
    def create_report(cls, org, user, json_dict):
        title = json_dict.get(Report.TITLE)
        description = json_dict.get(Report.DESCRIPTION)
        config = json_dict.get(Report.CONFIG)
        id = json_dict.get(Report.ID)

        existing = cls.objects.filter(pk=id, org=org)
        if existing:
            existing.update(title=title, description=description, config=config)

            return cls.objects.get(pk=id)

        return cls.objects.create(
            title=title, description=description, config=config, org=org, created_by=user, modified_by=user
        )

    def as_json(self):
        return dict(
            text=self.title, id=self.pk, description=self.description, config=self.config, public=self.is_published
        )

    @classmethod
    def build_uuid_to_category_map(cls, categories):
        # categories --> [{'count': 0, 'label': 'Yes'}]
        # uuid_to_category --> {'ed851f55-fe90-411f-9658-c1558798d14f': 'Yes'}
        if not categories:
            return list, dict

        categories_result = []
        uuid_to_category_result = {}

        for item in categories:
            category_item = dict(count=0, label=item.get("name"))
            categories_result.append(category_item)

            uuid_to_category_result[item.get("uuid")] = item.get("name")

        return categories_result, uuid_to_category_result

    @classmethod
    def _filtered_values_to_categories(cls, contacts, contact_field, formatter=None, return_contacts=False):
        set_contacts = set()

        return [], set()

    @classmethod
    def get_filtered_value_summary(
        cls, org, ruleset=None, contact_field=None, filters=None, return_contacts=False, filter_contacts=None
    ):
        """
        Return summary results for the passed in values, optionally filtering by a passed in filter on the contact.
        (len(set_contacts), len(unset_contacts), categories)

        This will try to aggregate results based on the values found.

        Filters expected in the following formats:
            { ruleset: rulesetId, categories: ["Red", "Blue", "Yellow"] }
            { groups: 12,124,15 }
            { location: 1515, boundary: "f1551" }
            { contact_field: fieldId, values: ["UK", "RW"] }
        """
        from temba.contacts.models import Contact

        # caller may identify either a ruleset or contact field to summarize
        if (not ruleset and not contact_field) or (ruleset and contact_field):  # pragma: needs cover
            raise ValueError("Must define either a RuleSet or ContactField to summarize values for")

        if ruleset:
            (categories, uuid_to_category) = cls.build_uuid_to_category_map(categories=ruleset.get("categories"))

        # this is for the case when we are filtering across our own categories, we build up the category uuids we will
        # pay attention then filter before we grab the actual values
        self_filter_uuids = []

        org_contacts = Contact.objects.filter(org=org, status=Contact.STATUS_ACTIVE)

        if filters:
            if filter_contacts is None:
                contacts = org_contacts
            else:  # pragma: needs cover
                contacts = Contact.objects.filter(pk__in=filter_contacts)

            for contact_filter in filters:
                # empty filters are no-ops
                if not contact_filter:
                    continue

                # we are filtering by another rule
                if "ruleset" in contact_filter:
                    # Tried to simulate this statement on 1.0 and couldn't find ruleset in contact_filter,
                    # so let's skip it for now
                    pass

                # we are filtering by one or more groups
                elif "groups" in contact_filter:
                    # filter our contacts by that group
                    for group_id in contact_filter.get("groups", []):
                        contacts = contacts.filter(all_groups__uuid=group_id)

                # we are filtering by one or more admin boundaries
                elif "boundary" in contact_filter:
                    # We will skip boundary filter since we don't work with OSM integration for our orgs
                    pass

                # we are filtering by a contact field
                elif "contact_field" in contact_filter:
                    contact_query = Q()

                    # we can't use __in as we want case insensitive matching
                    for value in contact_filter.get("values", []):
                        search_filter = f'"{contact_filter["contact_field"]}":"{value}"'
                        contact_query |= Q(fields__contains=search_filter)

                    contacts = contacts.filter(contact_query)

                else:  # pragma: needs cover
                    raise ValueError("Invalid filter definition, must include 'group' or 'contact_field'")

            contacts = set([c["id"] for c in contacts.values("id")])

        else:
            # no filter, default either to all contacts or our filter contacts
            if filter_contacts:
                contacts = filter_contacts
            else:
                contacts = set([c["id"] for c in org_contacts.values("id")])

        # we are summarizing a flow ruleset
        if ruleset:
            # TODO we need to implement this to search on the new model
            #  where we will save the collected results from the flow runs once a day
            pass

        # we are summarizing based on contact field
        else:
            # Tried to simulate this statement on 1.0 and couldn't find contact field on filters,
            # so let's skip it for now
            pass

        return 0, 0, []

    @classmethod
    def get_value_summary(cls, org, ruleset=None, contact_field=None, filters=None, segment=None):
        """
        Returns the results for the passed in ruleset or contact field given the passed in filters and segments.

        Filters are expected in the following formats:
            { field: rulesetId, categories: ["Red", "Blue", "Yellow"] }

        Segments are expected in these formats instead:
            { ruleset: 123e4567-e89b-12d3-a456-426614174000, categories: ["Red", "Blue"] }  // segmenting by another field, for those categories
            { groups: 124,151,151 }                         // segment by each each group in the passed in ids
            { location: "State", parent: null }             // segment for each admin boundary within the parent
            { contact_field: "Country", values: ["US", "EN", "RW"] } // segment by a contact field for these values
        """
        results = []

        if (not ruleset and not contact_field) or (ruleset and contact_field):  # pragma: needs cover
            raise ValueError("Must specify either a RuleSet or Contact field.")

        open_ended = ruleset and ruleset.get("type") == "switch" and len(ruleset.get("categories", [])) == 1

        # default our filters to an empty list if None are passed in
        if filters is None:
            filters = []

        # build the kwargs for our subcall
        kwargs = dict(org=org, ruleset=ruleset, contact_field=contact_field, filters=filters)

        # this is our list of dependencies, that is things that will blow away our results
        dependencies = set()
        fingerprint_dict = dict(filters=filters, segment=segment)

        if ruleset:
            fingerprint_dict["ruleset"] = ruleset.get("uuid")
            dependencies.add(RULESET_KEY % ruleset.get("uuid"))

        if contact_field:
            fingerprint_dict["contact_field"] = contact_field.uuid
            dependencies.add(CONTACT_KEY % contact_field.uuid)

        for contact_filter in filters:
            if "ruleset" in contact_filter:
                dependencies.add(RULESET_KEY % contact_filter["ruleset"])
            if "groups" in contact_filter:
                for group_id in contact_filter["groups"]:
                    dependencies.add(GROUP_KEY % group_id)
            if "location" in contact_filter:  # pragma: needs cover
                # we will not implement this filter since we don't use it on our clients
                pass

        if segment:
            if "ruleset" in segment:
                dependencies.add(RULESET_KEY % segment["ruleset"])
            if "groups" in segment:  # pragma: needs cover
                for group_id in segment["groups"]:
                    dependencies.add(GROUP_KEY % group_id)
            if "location" in segment:
                # we will not implement this filter since we don't use it on our clients
                pass

        # our final redis key will contain each dependency as well as a HASH representing the fingerprint of the
        # kwargs passed to this method, generate that hash
        fingerprint = hash(json.dumps(fingerprint_dict))

        # generate our key
        key = f"{VALUE_SUMMARY_CACHE_KEY}{':'.join(sorted(list(dependencies)))}:{str(fingerprint)}"

        # does our value exist?
        r = get_redis_connection()
        cached = r.get(key)

        if cached is not None:
            try:
                return json.loads(cached)
            except Exception:  # pragma: needs cover
                # failed decoding, oh well, go calculate it instead
                pass

        if segment:
            # segmenting a result is the same as calculating the result with the addition of each
            # category as a filter so we expand upon the passed in filters to do this
            if "ruleset" in segment and "categories" in segment:
                for category in segment.get("categories", []):
                    category_filter = list(filters)
                    category_filter.append(dict(ruleset=segment.get("ruleset"), categories=[category]))

                    # calculate our results for this segment
                    kwargs["filters"] = category_filter
                    (set_count, unset_count, categories) = cls.get_filtered_value_summary(**kwargs)
                    results.append(
                        dict(
                            label=category,
                            open_ended=open_ended,
                            set=set_count,
                            unset=unset_count,
                            categories=categories,
                        )
                    )

            # segmenting by groups instead, same principle but we add group filters
            elif "groups" in segment:  # pragma: needs cover
                for group_id in segment["groups"]:
                    # load our group
                    group = ContactGroup.user_groups.get(org=org, uuid=group_id)

                    category_filter = list(filters)
                    category_filter.append(dict(groups=[group_id]))

                    # calculate our results for this segment
                    kwargs["filters"] = category_filter
                    (set_count, unset_count, categories) = cls.get_filtered_value_summary(**kwargs)
                    results.append(
                        dict(
                            label=group.name,
                            open_ended=open_ended,
                            set=set_count,
                            unset_count=unset_count,
                            categories=categories,
                        )
                    )

            # segmenting by a contact field, only for passed in categories
            elif "contact_field" in segment and "values" in segment:
                # look up the contact field
                field = ContactField.get_by_label(org, segment["contact_field"])

                for value in segment["values"]:
                    value_filter = list(filters)
                    value_filter.append(dict(contact_field=field.uuid, values=[value]))

                    # calculate our results for this segment
                    kwargs["filters"] = value_filter
                    (set_count, unset_count, categories) = cls.get_filtered_value_summary(**kwargs)
                    results.append(
                        dict(
                            label=value, open_ended=open_ended, set=set_count, unset=unset_count, categories=categories
                        )
                    )

        else:
            (set_count, unset_count, categories) = cls.get_filtered_value_summary(**kwargs)

            # TODO Check whether we have rules/categories
            # if ruleset and len(ruleset.get_rules()) == 1 and isinstance(ruleset.get_rules()[0].test, TrueTest):

            results.append(
                dict(
                    label=str(_("All")), open_ended=open_ended, set=set_count, unset=unset_count, categories=categories
                )
            )

        # for each of our dependencies, add our key as something that depends on it
        pipe = r.pipeline()
        for dependency in dependencies:
            pipe.sadd(dependency, key)
            pipe.expire(dependency, VALUE_SUMMARY_CACHE_TIME)

        # and finally set our result
        pipe.set(key, json.dumps(results), VALUE_SUMMARY_CACHE_TIME)
        pipe.execute()

        return results

    def __str__(self):  # pragma: needs cover
        return "%s - %s" % (self.pk, self.title)

    class Meta:
        unique_together = (("org", "title"),)
