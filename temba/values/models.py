from collections import Counter, defaultdict
import json
from django.db import models, connection
from django.db.models import Count
from redis_cache import get_redis_connection
from temba.orgs.models import Org
from temba.locations.models import AdminBoundary
from django.utils.translation import ugettext_lazy as _
from temba.utils import format_decimal, get_dict_from_cursor, dict_to_json, json_to_dict
from stop_words import get_stop_words
import time

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

    rule_uuid = models.CharField(max_length=255, null=True, db_index=True,
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
    def _filtered_values_to_categories(cls, contacts, values, label_field, formatter=None, return_contacts=False):
        value_contacts = defaultdict(list)
        for value in values:
            if value['contact'] in contacts:
                if formatter:
                    label = formatter(value[label_field])
                else:
                    label = value[label_field]

                value_contacts[label].append(value['contact'])

        categories = []
        for value, contacts in value_contacts.items():
            category = dict(label=value, count=len(contacts))
            if return_contacts:
                category['contacts'] = contacts

            categories.append(category)

        # sort our categories by our count decreasing
        return sorted(categories, key=lambda c: c['count'], reverse=True)

    @classmethod
    def get_filtered_value_summary(cls, ruleset=None, contact_field=None, filters=None, return_contacts=False, filter_contacts=None):
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

        start = time.time()

        # caller may identify either a ruleset or contact field to summarize
        if (not ruleset and not contact_field) or (ruleset and contact_field):
            raise Exception("Must define either a RuleSet or ContactField to summarize values for")

        if ruleset:
            (categories, uuid_to_category) = ruleset.build_uuid_to_category_map()

        org = ruleset.flow.org if ruleset else contact_field.org

        # this is for the case when we are filtering across our own categories, we build up the category uuids we will
        # pay attention then filter before we grab the actual values
        self_filter_uuids = []

        org_contacts = Contact.objects.filter(org=org, is_test=False, is_active=True)

        if filters:
            if filter_contacts is None:
                contacts = org_contacts
            else:
                contacts = Contact.objects.filter(pk__in=filter_contacts)

            for filter in filters:
                # empty filters are no-ops
                if not filter:
                    continue

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
                elif 'boundary':
                    boundaries = filter['boundary']
                    if not isinstance(boundaries, list):
                        boundaries = [boundaries]

                    # filter our contacts by those that are in that location boundary
                    contacts = contacts.filter(values__contact_field__id=filter['location'],
                                               values__location_value__osm_id__in=boundaries)

                else:
                    raise Exception("Invalid filter definition, must include 'group', 'ruleset' or 'boundary'")

            contacts = set([c['id'] for c in contacts.values('id')])

        else:
            # no filter, default either to all contacts or our filter contacts
            if filter_contacts:
                contacts = filter_contacts
            else:
                contacts = set([c['id'] for c in org_contacts.values('id')])

        # we are summarizing a flow ruleset
        if ruleset:
            runs = FlowRun.objects.filter(flow=ruleset.flow).order_by('contact', '-created_on', '-pk').distinct('contact')
            runs = [r['id'] for r in runs.values('id', 'contact') if r['contact'] in contacts]

            # our dict will contain category name to count
            results = dict()

            # we can't use a subselect here because of a bug in Django, should be fixed in 1.7
            # see: https://code.djangoproject.com/ticket/22434
            value_counts = Value.objects.filter(org=org, ruleset=ruleset, rule_uuid__in=uuid_to_category.keys())

            # restrict our runs, using ANY is way quicker than IN
            if runs:
                value_counts = value_counts.extra(where=["run_id = ANY(VALUES " + ", ".join(["(%d)" % r for r in runs]) + ")"])

            # no matching runs, exclude any values
            else:
                value_counts = value_counts.extra(where=["1=0"])

            # contacts that have gotten to this step
            step_contacts = FlowStep.objects.filter(step_uuid=ruleset.uuid)

            # if we have runs to filter by, do so using ANY
            if runs:
                step_contacts = step_contacts.extra(where=["run_id = ANY(VALUES " + ", ".join(["(%d)" % r for r in runs]) + ")"])

            # otherwise, exclude all
            else:
                step_contacts = step_contacts.extra(where=["1=0"])

            step_contacts = set([s['run__contact'] for s in step_contacts.values('run__contact')])

            # restrict to our filter uuids if we are self filtering
            if self_filter_uuids:
                value_counts = value_counts.filter(rule_uuid__in=self_filter_uuids)

            values = value_counts.values('rule_uuid', 'contact')
            value_contacts = defaultdict(set)
            for value in values:
                value_contacts[value['rule_uuid']].add(value['contact'])

            results = defaultdict(set)
            for uuid, contacts in value_contacts.items():
                category = uuid_to_category[uuid]
                results[category] |= contacts

            # now create an ordered array of our results
            set_contacts = set()
            for category in categories:
                contacts = results.get(category['label'], set())
                if return_contacts:
                    category['contacts'] = contacts

                category['count'] = len(contacts)
                set_contacts |= contacts

            # how many runs actually entered a response?
            set_contacts = set_contacts
            unset_contacts = step_contacts - set_contacts

        # we are summarizing based on contact field
        else:
            set_contacts = contacts & set([v['contact'] for v in Value.objects.filter(contact_field=contact_field).values('contact')])
            unset_contacts = contacts - set_contacts

            values = Value.objects.filter(contact_field=contact_field)

            if contact_field.value_type == TEXT:
                values = values.values('string_value', 'contact')
                categories = cls._filtered_values_to_categories(contacts, values, 'string_value',
                                                                return_contacts=return_contacts)

            elif contact_field.value_type == DECIMAL:
                values = values.values('decimal_value', 'contact')
                categories = cls._filtered_values_to_categories(contacts, values, 'decimal_value',
                                                                formatter=format_decimal, return_contacts=return_contacts)

            elif contact_field.value_type == DATETIME:
                values = values.extra({'date_value': "date_trunc('day', datetime_value)"}).values('date_value', 'contact')
                categories = cls._filtered_values_to_categories(contacts, values, 'date_value',
                                                                return_contacts=return_contacts)

            elif contact_field.value_type in [STATE, DISTRICT]:
                values = values.values('location_value__osm_id', 'contact')
                categories = cls._filtered_values_to_categories(contacts, values, 'location_value__osm_id',
                                                                return_contacts=return_contacts)

            else:
                raise Exception(_("Summary of contact fields with value type of %s is not supported" % contact_field.get_value_type_display()))

        print "RulesetSummary [%f]: %s contact_field: %s with filters: %s" % (time.time() - start, ruleset, contact_field, filters)

        if return_contacts:
            return (set_contacts, unset_contacts, categories)
        else:
            return (len(set_contacts), len(unset_contacts), categories)

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
    def get_value_summary(cls, ruleset=None, contact_field=None, filters=None, segment=None):
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

        start = time.time()
        results = []

        if (not ruleset and not contact_field) or (ruleset and contact_field):
            raise Exception("Must specify either a RuleSet or Contact field.")

        org = ruleset.flow.org if ruleset else contact_field.org

        open_ended = ruleset and len(ruleset.get_rules()) == 1

        # default our filters to an empty list if None are passed in
        if filters is None:
            filters = []

        # build the kwargs for our subcall
        kwargs = dict(ruleset=ruleset, contact_field=contact_field, filters=filters)

        # this is our list of dependencies, that is things that will blow away our results
        dependencies = set()
        fingerprint_dict = dict(filters=filters, segment=segment)
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

                # get all the boundaries we are segmenting on
                boundaries = list(AdminBoundary.objects.filter(parent=parent).order_by('name'))

                # if the field is a district field, they need to specify the parent state
                if not parent_osm_id and field.value_type == DISTRICT:
                    raise Exception(_("You must specify a parent state to segment results by district"))

                # if this is a district, we can speed things up by only including those districts in our parent, build
                # the filter for that
                if parent and field.value_type == DISTRICT:
                    location_filters = [filters, dict(location=field.pk, boundary=[b.osm_id for b in boundaries])]
                else:
                    location_filters = filters

                # get all the contacts segment by location first
                (location_set_contacts, location_unset_contacts, location_results) = \
                    cls.get_filtered_value_summary(contact_field=field, filters=location_filters, return_contacts=True)

                # now get the contacts for our primary query
                kwargs['return_contacts'] = True
                kwargs['filter_contacts'] = location_set_contacts
                (primary_set_contacts, primary_unset_contacts, primary_results) = cls.get_filtered_value_summary(**kwargs)

                # build a map of osm_id to location_result
                osm_results = {lr['label']: lr for lr in location_results}
                empty_result = dict(contacts=list())

                for boundary in boundaries:
                    location_result = osm_results.get(boundary.osm_id, empty_result)

                    # clone our primary results
                    segmented_results = dict(label=boundary.name,
                                             boundary=boundary.osm_id,
                                             open_ended=open_ended)

                    location_categories = list()
                    location_contacts = set(location_result['contacts'])

                    for category in primary_results:
                        category_contacts = set(category['contacts'])

                        intersection = location_contacts & category_contacts
                        location_categories.append(dict(label=category['label'], count=len(intersection)))

                    segmented_results['set'] = len(location_contacts & primary_set_contacts)
                    segmented_results['unset'] = len(location_contacts & primary_unset_contacts)
                    segmented_results['categories'] = location_categories
                    results.append(segmented_results)

                results = sorted(results, key=lambda r: r['label'])

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
                categories = sorted(categories, key=lambda c: (-c['count'], c['label']))

            results.append(dict(label=unicode(_("All")), open_ended=open_ended, set=set_count, unset=unset_count, categories=categories))

        # cache this result set
        r.set(key, dict_to_json(results), VALUE_SUMMARY_CACHE_TIME)

        # leave me: nice for profiling..
        # from django.db import connection as db_connection, reset_queries
        # print "=" * 80
        # for query in db_connection.queries:
        #     print "%s - %s" % (query['time'], query['sql'][:100])
        # print "-" * 80
        # print "took: %f" % (time.time() - start)
        # print "=" * 80
        # reset_queries()

        return results

    def __unicode__(self):
        if self.ruleset:
            return "Contact: %d - %s = %s" % (self.contact.pk, self.ruleset.label, self.category)
        elif self.contact_field:
            return "Contact: %d - %s = %s" % (self.contact.pk, self.contact_field.label, self.string_value)
        else:
            return "Contact: %d - %s" % (self.contact.pk, self.string_value)
