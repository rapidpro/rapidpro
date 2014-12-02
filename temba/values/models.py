import json
from django.db import models, connection
from django.db.models import Count
from redis_cache import get_redis_connection
from temba.orgs.models import Org
from temba.locations.models import AdminBoundary
from django.utils.translation import ugettext_lazy as _
from temba.utils import format_decimal, get_dict_from_cursor, dict_to_json, json_to_dict
from stop_words import get_stop_words

TEXT = 'T'
DECIMAL = 'N'
DATETIME = 'D'
STATE = 'S'
DISTRICT = 'I'

VALUE_TYPE_CHOICES = ((TEXT, "Text"),
                      (DECIMAL, "Numeric"),
                      (DATETIME, "Date & Time"),
                      (STATE, "State"),
                      (DISTRICT, "District"))

VALUE_SUMMARY_CACHE_KEY = 'value_summary'
CONTACT_KEY = 'vsc%d'
GROUP_KEY = 'vsg%d'
RULESET_KEY = 'vsr%d'

# cache for up to 30 days (we will invalidate manually when dependencies change)
VALUE_SUMMARY_CACHE_TIME = 60 * 60 * 24 * 30

class Value(models.Model):

    """
    A Value is created to store the most recent result for a step in a flow. Value will store typed
    values of the raw text that was received during the flow.
    """

    contact = models.ForeignKey('contacts.Contact', related_name='values')

    contact_field = models.ForeignKey('contacts.ContactField', null=True, on_delete=models.SET_NULL,
                                      help_text="The ContactField this value is for, if any")

    ruleset = models.ForeignKey('flows.RuleSet', null=True, on_delete=models.SET_NULL,
                               help_text="The RuleSet this value is for, if any")

    run = models.ForeignKey('flows.FlowRun', null=True, on_delete=models.SET_NULL, related_name='values',
                            help_text="The FlowRun this value is for, if any")

    rule_uuid = models.CharField(max_length=255, null=True,
                                 help_text="The rule that matched, only appropriate for RuleSet values")

    category = models.CharField(max_length=36, null=True,
                                help_text="The name of the category this value matched in the RuleSet")

    string_value = models.TextField(max_length=640,
                                    help_text="The string value or string representation of this value")
    decimal_value = models.DecimalField(max_digits=36, decimal_places=8, null=True,
                                        help_text="The decimal value of this value if any.")
    datetime_value = models.DateTimeField(null=True,
                                          help_text="The datetime value of this value if any.")

    location_value = models.ForeignKey(AdminBoundary, on_delete=models.SET_NULL, null=True,
                                       help_text="The location value of this value if any.")

    recording_value = models.TextField(max_length=640, null=True,
                                 help_text="The recording url if any.")

    org = models.ForeignKey(Org)

    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)


    @classmethod
    def get_filtered_value_summary(self, ruleset=None, contact_field=None, filters=None, latest_only=True):
        """
        Return summary results for the passed in values, optionally filtering by a passed in filter on the contact.

        This will try to aggregate results based on the values found.

        Filters expected in the following formats:
            { ruleset: rulesetId, categories: ["Red", "Blue", "Yellow"] }
            { groups: 12,124,15 }
            { location: 1515, boundary: "f1551" }
        """
        from temba.flows.models import RuleSet, FlowRun, FlowStep
        from temba.contacts.models import Contact

        # caller my identify either a ruleset or contact field to summarize
        if (not ruleset and not contact_field) or (ruleset and contact_field):
            raise Exception("Must define either a RuleSet or ContactField to summarize values for")

        if ruleset:
            (categories, uuid_to_category) = ruleset.build_uuid_to_category_map()

        org = ruleset.flow.org if ruleset else contact_field.org

        # this is for the case when we are filtering across our own categories, we build up the category uuids we will
        # pay attention then filter before we grab the actual values
        self_filter_uuids = []

        # the contacts we are looking at
        contacts = Contact.objects.filter(org=org, is_test=False, is_active=True)
        if filters:
            for filter in filters:
                # we are filtering by another rule
                if 'ruleset' in filter:
                    # load the ruleset for this filter
                    filter_ruleset = RuleSet.objects.get(pk=filter['ruleset'])
                    (filter_cats, filter_uuids) = filter_ruleset.build_uuid_to_category_map()

                    uuids = []
                    for (uuid, category) in filter_uuids.items():
                        if category in filter['categories']:
                            uuids.append(uuid)

                    contacts = contacts.filter(values__rule_uuid__in=uuids)

                    # this is a self filter, save the uuids for later filtering
                    if ruleset and ruleset.pk == filter_ruleset.pk:
                        self_filter_uuids = uuids

                # we are filtering by one or more group
                elif 'groups' in filter:
                    # filter our contacts by that group
                    for group_id in filter['groups']:
                        contacts = contacts.filter(groups__pk=group_id)


                # we are filtering by one or more admin boundaries
                elif 'boundary' in filter:
                    # filter our contacts by those that are in that location boundary
                    contacts = contacts.filter(values__contact_field__id=filter['location'],
                                               values__location_value__osm_id=filter['boundary'])

                else:
                    raise Exception("Invalid filter definition, must include 'group' or 'ruleset'")

        # we are summarizing a flow ruleset
        if ruleset:
            if latest_only:
                runs = FlowRun.objects.filter(flow=ruleset.flow,
                                              contact__in=contacts).order_by('contact', '-created_on', '-pk').distinct('contact')
            else:
                runs = FlowRun.objects.filter(flow=ruleset.flow, contact__in=contacts)

            runs = [r.id for r in runs]

            # our dict will contain category name to count
            results = dict()

            # we can't use a subselect here because of a bug in Django, should be fixed in 1.7
            # see: https://code.djangoproject.com/ticket/22434
            value_counts = Value.objects.filter(org=org, ruleset=ruleset, rule_uuid__in=uuid_to_category.keys())

            # filter by our runs
            value_counts = value_counts.filter(run__in=runs)

            # of steps that are unset
            total = FlowStep.objects.filter(step_uuid=ruleset.uuid, run__in=runs).count()

            # how many runs actually entered a response?
            set_count = FlowStep.objects.filter(step_uuid=ruleset.uuid, run__in=runs, rule_uuid__in=uuid_to_category.keys()).count()
            unset_count = total - set_count

            # restrict to our filter uuids if we are self filtering
            if self_filter_uuids:
                value_counts = value_counts.filter(rule_uuid__in=self_filter_uuids)

            value_counts = value_counts.values('rule_uuid').annotate(rule_count=Count('rule_uuid'))

            for uuid_count in value_counts:
                category = uuid_to_category[uuid_count['rule_uuid']]
                count = results.get(category, 0)

                results[category] = count+uuid_count['rule_count']

            # now create an ordered array of our results
            for category in categories:
                category['count'] = results.get(category['label'], 0)

        # we are summarizing based on contact field
        else:
            # how many total contacts could have a value
            contacts = [c.id for c in contacts]

            total = len(contacts)
            set_count = Value.objects.filter(contact_field=contact_field, contact__in=contacts).count()
            unset_count = total - set_count

            categories = []

            value_counts = Value.objects.filter(contact_field=contact_field, contact__in=contacts)


            if contact_field.value_type == TEXT:
                value_counts = value_counts.values('string_value').annotate(value_count=Count('string_value')).order_by('-value_count', 'string_value')
                for count in value_counts:
                    categories.append(dict(label=count['string_value'], count=count['value_count']))

            elif contact_field.value_type == DECIMAL:
                value_counts = value_counts.values('decimal_value').annotate(value_count=Count('decimal_value')).order_by('-value_count', 'decimal_value')
                for count in value_counts:
                    categories.append(dict(label=format_decimal(count['decimal_value']), count=count['value_count']))

            elif contact_field.value_type == DATETIME:
                value_counts = value_counts.extra({'date_value': "date_trunc('day', datetime_value)"}).values('date_value').annotate(value_count=Count('id')).order_by('date_value')
                for count in value_counts:
                    categories.append(dict(label=count['date_value'], count=count['value_count']))

            elif contact_field.value_type in [STATE, DISTRICT]:
                value_counts = value_counts.values('location_value__osm_id').annotate(value_count=Count('location_value__osm_id')).order_by('-value_count', 'location_value__osm_id')
                for count in value_counts:
                    categories.append(dict(label=count['location_value__osm_id'], count=count['value_count']))

            else:
                raise Exception(_("Summary of contact fields with value type of %s is not supported" % contact_field.get_value_type_display()))

        return (set_count, unset_count, categories)

    @classmethod
    def invalidate_cache(cls, contact_field=None, ruleset=None, group=None):
        """
        Used to invalidate our summary cache for values. Callers should pass in one (and only one) of a contact field,
        ruleset or group that changed and all result summaries that have changed will be invalidated accordingly.
        :return: how many cached records were invalidated
        """
        if not contact_field and not ruleset and not group:
            raise Exception("You must specify a contact field, ruleset or group to invalidate results for")

        if contact_field:
            key = ':' + (CONTACT_KEY % contact_field.id) + ':'
        elif group:
            key = ':' + (GROUP_KEY % group.id) + ':'
        elif ruleset:
            key = ':' + (RULESET_KEY % ruleset.id) + ':'

        # blow away any redis items that contain our key as a dependency
        r = get_redis_connection()
        keys = r.keys(VALUE_SUMMARY_CACHE_KEY + "*" + key + "*")
        if keys:
            invalidated = r.delete(*keys)
        else:
            invalidated = 0

        return invalidated

    @classmethod
    def get_value_summary(cls, ruleset=None, contact_field=None, filters=None, segment=None, latest_only=True):
        """
        Returns the results for the passed in ruleset or contact field given the passed in filters and segments.

        Filters are expected in the following formats:
            { field: rulesetId, categories: ["Red", "Blue", "Yellow"] }

        Segments are expected in these formats instead:
            { ruleset: 1515, categories: ["Red", "Blue"] }  // segmenting by another field, for those categories
            { groups: 124,151,151 }                         // segment by each each group in the passed in ids
            { location: "State", parent: null }             // segment for each admin boundary within the parent
        """
        from temba.contacts.models import ContactGroup, ContactField
        from temba.flows.models import TrueTest

        results = []

        if (not ruleset and not contact_field) or (ruleset and contact_field):
            raise Exception("Must specify either a RuleSet or Contact field.")

        org = ruleset.flow.org if ruleset else contact_field.org

        open_ended = ruleset and len(ruleset.get_rules()) == 1

        # default our filters to an empty list if None are passed in
        if filters is None:
            filters = []

        # build the kwargs for our subcall
        kwargs = dict(ruleset=ruleset, contact_field=contact_field, filters=filters, latest_only=latest_only)

        # this is our list of dependencies, that is things that will blow away our results
        dependencies = set()
        fingerprint_dict = dict(filters=filters, segment=segment, latest_only=latest_only)
        if ruleset:
            fingerprint_dict['ruleset'] = ruleset.id
            dependencies.add(RULESET_KEY % ruleset.id)
        if contact_field:
            fingerprint_dict['contact_field'] = contact_field.id
            dependencies.add(CONTACT_KEY % contact_field.id)

        for filter in filters:
            if 'ruleset' in filter: dependencies.add('vsr%d' % filter['ruleset'])
            if 'groups' in filter:
                for group_id in filter['groups']:
                    dependencies.add(GROUP_KEY % group_id)
            if 'location' in filter:
                field = ContactField.objects.get(org=org, label__iexact=filter['location'])
                dependencies.add(CONTACT_KEY % field.id)

        if segment:
            if 'ruleset' in segment: dependencies.add('vsr%d' % segment['ruleset'])
            if 'groups' in segment:
                for group_id in segment['groups']:
                    dependencies.add(GROUP_KEY % group_id)
            if 'location' in segment:
                field = ContactField.objects.get(org=org, label__iexact=segment['location'])
                dependencies.add(CONTACT_KEY % field.id)

        # our final redis key will contain each dependency as well as a HASH representing the fingerprint of the
        # kwargs passed to this method, generate that hash
        fingerprint = hash(dict_to_json(fingerprint_dict))

        # generate our key
        key = VALUE_SUMMARY_CACHE_KEY + ":" + ":".join(sorted(list(dependencies))) + ":" + str(fingerprint)

        # does our value exist?
        r = get_redis_connection()
        cached = r.get(key)

        if not cached is None:
            try:
                return json_to_dict(cached)
            except:
                # failed decoding, oh well, go calculate it instead
                pass

        if segment:
            # segmenting a result is the same as calculating the result with the addition of each
            # category as a filter so we expand upon the passed in filters to do this
            if 'categories' in segment:
                for category in segment['categories']:
                    category_filter = list(filters)
                    category_filter.append(dict(ruleset=segment['ruleset'], categories=[category]))


                    # calculate our results for this segment
                    kwargs['filters'] = category_filter
                    (set_count, unset_count, categories) = cls.get_filtered_value_summary(**kwargs)
                    results.append(dict(label=category, open_ended=open_ended, set=set_count, unset=unset_count, categories=categories))


            # segmenting by groups instead, same principle but we add group filters
            elif 'groups' in segment:
                for group_id in segment['groups']:
                    # load our group
                    group = ContactGroup.objects.get(is_active=True, org=org, pk=group_id)

                    category_filter = list(filters)
                    category_filter.append(dict(groups=[group_id]))

                    # calculate our results for this segment
                    kwargs['filters'] = category_filter
                    (set_count, unset_count, categories) = cls.get_filtered_value_summary(**kwargs)
                    results.append(dict(label=group.name, open_ended=open_ended, set=set_count, unset_count=unset_count, categories=categories))


            # segmenting by a location field
            elif 'location' in segment:
                # look up the contact field
                field = ContactField.objects.get(org=org, label__iexact=segment['location'])

                # make sure they are segmenting on a location type that makes sense
                if not field.value_type in [STATE, DISTRICT]:
                    raise Exception(_("Cannot segment on location for field that is not a State or District type"))

                # make sure our org has a country for location based responses
                if not org.country:
                    raise Exception(_("Cannot segment by location until country has been selected for organization"))

                # the boundaries we will segment by
                parent = org.country

                # figure out our parent
                parent_osm_id = segment.get('parent', None)
                if parent_osm_id:
                    parent = AdminBoundary.objects.get(osm_id=parent_osm_id)

                # if the field is a district field, they need to specify the parent state
                if not parent_osm_id and field.value_type == DISTRICT:
                    raise Exception(_("You must specify a parent state to segment results by district"))

                # now segment by all the children of this parent
                for boundary in AdminBoundary.objects.filter(parent=parent).order_by('name'):
                    boundary_filter = list(filters)
                    boundary_filter.append(dict(location=field.id, boundary=boundary.osm_id))
                    kwargs['filters'] = boundary_filter

                    # calculate our results for this segment
                    (set_count, unset_count, categories) = cls.get_filtered_value_summary(**kwargs)
                    results.append(dict(label=boundary.name, boundary=boundary.osm_id, open_ended=open_ended,
                                        set=set_count, unset=unset_count, categories=categories))


        else:
            (set_count, unset_count, categories) = cls.get_filtered_value_summary(**kwargs)

            # Check we have and we have an OPEN ENDED ruleset
            if ruleset and len(ruleset.get_rules()) == 1 and isinstance(ruleset.get_rules()[0].test, TrueTest):
                cursor = connection.cursor()

                custom_sql = """
                  SELECT w.label, count(*) AS count FROM (
                    SELECT
                      regexp_split_to_table(LOWER(text), E'[^[:alnum:]_]') AS label
                    FROM msgs_msg
                    WHERE id IN (
                      SELECT
                        msg_id
                        FROM flows_flowstep_messages, flows_flowstep
                        WHERE flowstep_id = flows_flowstep.id AND
                        flows_flowstep.step_uuid = '%s'
                      )
                  ) w group by w.label order by count desc;
                """ % ruleset.uuid

                cursor.execute(custom_sql)
                unclean_categories = get_dict_from_cursor(cursor)
                categories = []
                ignore_words = get_stop_words('english')

                for category in unclean_categories:
                    if len(category['label']) > 1 and category['label'] not in ignore_words and len(categories) < 100:
                        categories.append(dict(label=category['label'], count=int(category['count'])))

                # sort by count, then alphabetically
                categories= sorted(categories, key=lambda c: (-c['count'], c['label']))

            results.append(dict(label=unicode(_("All")), open_ended=open_ended, set=set_count, unset=unset_count, categories=categories))

        # cache this result set
        r.set(key, dict_to_json(results), VALUE_SUMMARY_CACHE_TIME)

        return results

    def __unicode__(self):
        if self.ruleset:
            return "Contact: %d - %s = %s" % (self.contact.pk, self.ruleset.label, self.category)
        elif self.contact_field:
            return "Contact: %d - %s = %s" % (self.contact.pk, self.contact_field.label, self.string_value)
        else:
            return "Contact: %d - %s" % (self.contact.pk, self.string_value)
