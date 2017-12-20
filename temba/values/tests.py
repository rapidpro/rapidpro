from __future__ import unicode_literals

import json

from datetime import timedelta
from django.core.urlresolvers import reverse
from django.utils import timezone
from mock import patch
from temba.contacts.models import ContactField
from temba.flows.models import RuleSet
from temba.orgs.models import Language
from temba.tests import FlowFileTest
from .models import Value


class ResultTest(FlowFileTest):

    def assertResult(self, result, index, category, count):
        self.assertEqual(count, result['categories'][index]['count'])
        self.assertEqual(category, result['categories'][index]['label'])

    def test_field_results(self):
        c1 = self.create_contact("Contact1", '0788111111')
        c2 = self.create_contact("Contact2", '0788222222')
        c3 = self.create_contact("Contact3", '0788333333')
        self.create_contact("Contact4", '0788444444')

        # create a gender field that uses strings
        gender = ContactField.get_or_create(self.org, self.admin, 'gender', label="Gender", value_type=Value.TYPE_TEXT)

        c1.set_field(self.user, 'gender', "Male")
        c2.set_field(self.user, 'gender', "Female")
        c3.set_field(self.user, 'gender', "Female")

        result = Value.get_value_summary(contact_field=gender)[0]
        self.assertEqual(2, len(result['categories']))
        self.assertEqual(3, result['set'])
        self.assertEqual(2, result['unset'])  # this is two as we have the default contact created by our unit tests
        self.assertFalse(result['open_ended'])
        self.assertResult(result, 0, "Female", 2)
        self.assertResult(result, 1, "Male", 1)

        # create an born field that uses decimals
        born = ContactField.get_or_create(self.org, self.admin, 'born', label="Born", value_type=Value.TYPE_DECIMAL)
        c1.set_field(self.user, 'born', 1977)
        c2.set_field(self.user, 'born', 1990)
        c3.set_field(self.user, 'born', 1977)

        result = Value.get_value_summary(contact_field=born)[0]
        self.assertEqual(2, len(result['categories']))
        self.assertEqual(3, result['set'])
        self.assertEqual(2, result['unset'])
        self.assertFalse(result['open_ended'])
        self.assertResult(result, 0, "1977", 2)
        self.assertResult(result, 1, "1990", 1)

        # ok, state field!
        state = ContactField.get_or_create(self.org, self.admin, 'state', label="State", value_type=Value.TYPE_STATE)
        c1.set_field(self.user, 'state', "Kigali City")
        c2.set_field(self.user, 'state', "Kigali City")

        result = Value.get_value_summary(contact_field=state)[0]
        self.assertEqual(1, len(result['categories']))
        self.assertEqual(2, result['set'])
        self.assertEqual(3, result['unset'])
        self.assertResult(result, 0, "1708283", 2)

        reg_date = ContactField.get_or_create(self.org, self.admin, 'reg_date', label="Registration Date", value_type=Value.TYPE_DATETIME)
        now = timezone.now()

        c1.set_field(self.user, 'reg_date', now.replace(hour=9))
        c2.set_field(self.user, 'reg_date', now.replace(hour=4))
        c3.set_field(self.user, 'reg_date', now - timedelta(days=1))
        result = Value.get_value_summary(contact_field=reg_date)[0]
        self.assertEqual(2, len(result['categories']))
        self.assertEqual(3, result['set'])
        self.assertEqual(2, result['unset'])
        self.assertResult(result, 0, now.replace(hour=0, minute=0, second=0, microsecond=0), 2)
        self.assertResult(result, 1, (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0), 1)

        # make sure categories returned are sorted by count, not name
        c2.set_field(self.user, 'gender', "Male")
        result = Value.get_value_summary(contact_field=gender)[0]
        self.assertEqual(2, len(result['categories']))
        self.assertEqual(3, result['set'])
        self.assertEqual(2, result['unset'])  # this is two as we have the default contact created by our unit tests
        self.assertFalse(result['open_ended'])
        self.assertResult(result, 0, "Male", 2)
        self.assertResult(result, 1, "Female", 1)

        # check the modified date is tracked for fields
        original_value = Value.objects.get(contact=c1, contact_field=gender)
        c1.set_field(self.user, 'gender', 'unknown')
        new_value = Value.objects.get(contact=c1, contact_field=gender)
        self.assertTrue(new_value.modified_on > original_value.modified_on)
        self.assertNotEqual(new_value.string_value, original_value.string_value)

    def run_color_gender_flow(self, contact, color, gender, age):
        self.assertEqual(self.send_message(self.flow, color, contact=contact, restart_participants=True), "What is your gender?")
        self.assertEqual(self.send_message(self.flow, gender, contact=contact), "What is your age?")
        self.assertEqual(self.send_message(self.flow, age, contact=contact), "Thanks.")

    def setup_color_gender_flow(self):
        self.flow = self.get_flow('color_gender_age')

        (self.c1, self.c2, self.c3, self.c4) = (self.create_contact("Contact1", '0788111111'),
                                                self.create_contact("Contact2", '0788222222'),
                                                self.create_contact("Contact3", '0788333333'),
                                                self.create_contact("Contact4", '0788444444'))

    def test_category_results(self):
        self.setup_color_gender_flow()

        # create a state field:
        # assign c1 and c2 to Kigali
        ContactField.get_or_create(self.org, self.admin, 'state', label="State", value_type=Value.TYPE_STATE)
        ContactField.get_or_create(self.org, self.admin, 'district', label="District", value_type=Value.TYPE_DISTRICT)

        self.c1.set_field(self.user, 'state', "Kigali City")
        self.c1.set_field(self.user, 'district', "Nyarugenge")
        self.c2.set_field(self.user, 'state', "Kigali City")
        self.c2.set_field(self.user, 'district', "Nyarugenge")

        self.run_color_gender_flow(self.c1, "red", "male", "16")
        self.run_color_gender_flow(self.c2, "blue", "female", "19")
        self.run_color_gender_flow(self.c3, "green", "male", "75")
        self.run_color_gender_flow(self.c4, "maroon", "female", "50")

        # create a group of the women
        ladies = self.create_group("Ladies", [self.c2, self.c4])

        # get our rulesets
        color = RuleSet.objects.get(flow=self.flow, label="Color")
        gender = RuleSet.objects.get(flow=self.flow, label="Gender")
        age = RuleSet.objects.get(flow=self.flow, label="Age")

        # fetch our results through the view
        self.login(self.admin)
        response = self.client.get(reverse('flows.ruleset_results', args=[color.pk]))
        response = response.json()

        categories = response['results'][0]['categories']
        self.assertEqual('Red', categories[0]['label'])
        self.assertEqual('Blue', categories[1]['label'])
        self.assertEqual('Green', categories[2]['label'])

        self.assertEqual(2, categories[0]['count'])
        self.assertEqual(1, categories[1]['count'])
        self.assertEqual(1, categories[2]['count'])

        # categories should be in the same order as our rules, should have correct counts
        result = Value.get_value_summary(ruleset=color)[0]
        self.assertEqual(3, len(result['categories']))
        self.assertFalse(result['open_ended'])
        self.assertResult(result, 0, "Red", 2)
        self.assertResult(result, 1, "Blue", 1)
        self.assertResult(result, 2, "Green", 1)

        # check our age category as well
        result = Value.get_value_summary(ruleset=age)[0]
        self.assertEqual(3, len(result['categories']))
        self.assertFalse(result['open_ended'])
        self.assertResult(result, 0, "Child", 1)
        self.assertResult(result, 1, "Adult", 2)
        self.assertResult(result, 2, "Senior", 1)

        # and our gender categories
        result = Value.get_value_summary(ruleset=gender)[0]
        self.assertEqual(2, len(result['categories']))
        self.assertFalse(result['open_ended'])
        self.assertResult(result, 0, "Male", 2)
        self.assertResult(result, 1, "Female", 2)

        # now filter the results and only get responses by men
        result = Value.get_value_summary(ruleset=color, filters=[dict(ruleset=gender.pk, categories=["Male"])])[0]
        self.assertResult(result, 0, "Red", 1)
        self.assertResult(result, 1, "Blue", 0)
        self.assertResult(result, 2, "Green", 1)

        # what about men that are adults?
        result = Value.get_value_summary(ruleset=color, filters=[dict(ruleset=gender.pk, categories=["Male"]),
                                         dict(ruleset=age.pk, categories=["Adult"])])[0]
        self.assertResult(result, 0, "Red", 0)
        self.assertResult(result, 1, "Blue", 0)
        self.assertResult(result, 2, "Green", 0)

        # union of all genders
        result = Value.get_value_summary(ruleset=color, filters=[dict(ruleset=gender.pk, categories=["Male", "Female"]),
                                         dict(ruleset=age.pk, categories=["Adult"])])[0]

        self.assertResult(result, 0, "Red", 1)
        self.assertResult(result, 1, "Blue", 1)
        self.assertResult(result, 2, "Green", 0)

        # just women adults by group
        result = Value.get_value_summary(ruleset=color, filters=[dict(groups=[ladies.pk]), dict(ruleset=age.pk, categories="Adult")])[0]

        self.assertResult(result, 0, "Red", 1)
        self.assertResult(result, 1, "Blue", 1)
        self.assertResult(result, 2, "Green", 0)

        # remove one of the women from the group
        ladies.update_contacts(self.user, [self.c2], False)

        # get a new summary
        result = Value.get_value_summary(ruleset=color, filters=[dict(groups=[ladies.pk]), dict(ruleset=age.pk, categories="Adult")])[0]

        self.assertResult(result, 0, "Red", 1)
        self.assertResult(result, 1, "Blue", 0)
        self.assertResult(result, 2, "Green", 0)

        # ok, back in she goes
        ladies.update_contacts(self.user, [self.c2], True)

        # do another run for contact 1
        self.run_color_gender_flow(self.c1, "blue", "male", "16")

        # totals should reflect the new value, not the old
        result = Value.get_value_summary(ruleset=color)[0]
        self.assertResult(result, 0, "Red", 1)
        self.assertResult(result, 1, "Blue", 2)
        self.assertResult(result, 2, "Green", 1)

        # what if we do a partial run?
        self.send_message(self.flow, "red", contact=self.c1, restart_participants=True)

        # should change our male/female breakdown since c1 now no longer has a gender
        result = Value.get_value_summary(ruleset=gender)[0]
        self.assertEqual(2, len(result['categories']))
        self.assertResult(result, 0, "Male", 1)
        self.assertResult(result, 1, "Female", 2)

        # back to a full flow
        self.run_color_gender_flow(self.c1, "blue", "male", "16")

        # ok, now segment by gender
        result = Value.get_value_summary(ruleset=color, filters=[], segment=dict(ruleset=gender.pk, categories=["Male", "Female"]))
        male_result = result[0]
        self.assertResult(male_result, 0, "Red", 0)
        self.assertResult(male_result, 1, "Blue", 1)
        self.assertResult(male_result, 2, "Green", 1)

        female_result = result[1]
        self.assertResult(female_result, 0, "Red", 1)
        self.assertResult(female_result, 1, "Blue", 1)
        self.assertResult(female_result, 2, "Green", 0)

        # segment by gender again, but use the contact field to do so
        result = Value.get_value_summary(ruleset=color, filters=[], segment=dict(contact_field="Gender", values=["MALE", "Female"]))
        male_result = result[0]
        self.assertResult(male_result, 0, "Red", 0)
        self.assertResult(male_result, 1, "Blue", 1)
        self.assertResult(male_result, 2, "Green", 1)

        female_result = result[1]
        self.assertResult(female_result, 0, "Red", 1)
        self.assertResult(female_result, 1, "Blue", 1)
        self.assertResult(female_result, 2, "Green", 0)

        # add in a filter at the same time
        result = Value.get_value_summary(ruleset=color, filters=[dict(ruleset=color.pk, categories=["Blue"])],
                                         segment=dict(ruleset=gender.pk, categories=["Male", "Female"]))

        male_result = result[0]
        self.assertResult(male_result, 0, "Red", 0)
        self.assertResult(male_result, 1, "Blue", 1)
        self.assertResult(male_result, 2, "Green", 0)

        female_result = result[1]
        self.assertResult(female_result, 0, "Red", 0)
        self.assertResult(female_result, 1, "Blue", 1)
        self.assertResult(female_result, 2, "Green", 0)

        # ok, try segmenting by location instead
        result = Value.get_value_summary(ruleset=color, segment=dict(location="State"))

        eastern_result = result[0]
        self.assertEqual('171591', eastern_result['boundary'])
        self.assertEqual('Eastern Province', eastern_result['label'])
        self.assertResult(eastern_result, 0, "Red", 0)
        self.assertResult(eastern_result, 1, "Blue", 0)
        self.assertResult(eastern_result, 2, "Green", 0)

        kigali_result = result[1]
        self.assertEqual('1708283', kigali_result['boundary'])
        self.assertEqual('Kigali City', kigali_result['label'])
        self.assertResult(kigali_result, 0, "Red", 0)
        self.assertResult(kigali_result, 1, "Blue", 2)
        self.assertResult(kigali_result, 2, "Green", 0)

        # updating state location leads to updated data
        self.c2.set_field(self.user, 'state', "Eastern Province")
        result = Value.get_value_summary(ruleset=color, segment=dict(location="State"))

        eastern_result = result[0]
        self.assertEqual('171591', eastern_result['boundary'])
        self.assertEqual('Eastern Province', eastern_result['label'])
        self.assertResult(eastern_result, 0, "Red", 0)
        self.assertResult(eastern_result, 1, "Blue", 1)
        self.assertResult(eastern_result, 2, "Green", 0)

        kigali_result = result[1]
        self.assertEqual('1708283', kigali_result['boundary'])
        self.assertEqual('Kigali City', kigali_result['label'])
        self.assertResult(kigali_result, 0, "Red", 0)
        self.assertResult(kigali_result, 1, "Blue", 1)
        self.assertResult(kigali_result, 2, "Green", 0)

        # segment by district instead
        result = Value.get_value_summary(ruleset=color, segment=dict(parent="1708283", location="District"))

        # only on district in kigali
        self.assertEqual(1, len(result))
        kigali_result = result[0]
        self.assertEqual('3963734', kigali_result['boundary'])
        self.assertEqual('Nyarugenge', kigali_result['label'])
        self.assertResult(kigali_result, 0, "Red", 0)
        self.assertResult(kigali_result, 1, "Blue", 2)
        self.assertResult(kigali_result, 2, "Green", 0)

        # do a sanity check on our choropleth view
        self.login(self.admin)
        response = self.client.get(reverse('flows.ruleset_choropleth', args=[color.pk]) +
                                   "?_format=json&boundary=" + self.org.country.osm_id)

        # response should be valid json
        response = response.json()

        # should have breaks
        self.assertTrue('breaks' in response)

        # should have two categories, Blue and Others
        self.assertEqual(2, len(response['categories']))
        self.assertEqual("Blue", response['categories'][0])
        self.assertEqual("Others", response['categories'][1])

        # assert our kigali result
        kigali_result = response['scores']['1708283']
        self.assertEqual(1, kigali_result['score'])
        self.assertEqual("Kigali City", kigali_result['name'])
        self.assertEqual("Blue", kigali_result['results'][0]['label'])
        self.assertEqual("Others", kigali_result['results'][1]['label'])

        self.assertEqual(1, kigali_result['results'][0]['count'])
        self.assertEqual(0, kigali_result['results'][1]['count'])

        self.assertEqual(100, kigali_result['results'][0]['percentage'])
        self.assertEqual(0, kigali_result['results'][1]['percentage'])

        with patch('temba.values.models.Value.get_value_summary') as mock:
            mock.return_value = []

            response = self.client.get(reverse('flows.ruleset_choropleth', args=[color.pk]) +
                                       "?_format=json&boundary=" + self.org.country.osm_id)

            # response should be valid json
            response = response.json()

            # should have two categories, Blue and Others
            self.assertEqual(2, len(response['categories']))
            self.assertEqual("", response['categories'][0])
            self.assertEqual("", response['categories'][1])

            # all counts and percentage are 0
            self.assertEqual(0, response['totals']['count'])
            self.assertEqual(0, response['totals']['results'][0]['count'])
            self.assertEqual(0, response['totals']['results'][0]['percentage'])
            self.assertEqual(0, response['totals']['results'][1]['count'])
            self.assertEqual(0, response['totals']['results'][1]['percentage'])

            # and empty string labels
            self.assertEqual("", response['totals']['results'][0]['label'])
            self.assertEqual("", response['totals']['results'][1]['label'])

        # also check our analytics view
        response = self.client.get(reverse('flows.ruleset_analytics'))

        # make sure we have only one flow in it
        flows = json.loads(response.context['flows'])
        self.assertEqual(1, len(flows))
        self.assertEqual(3, len(flows[0]['rules']))

    def test_open_ended_word_frequencies(self):
        flow = self.get_flow('random_word')

        def run_flow(contact, word):
            self.assertEqual("Thank you", self.send_message(flow, word, contact=contact, restart_participants=True))

        (c1, c2, c3, c4, c5, c6) = (self.create_contact("Contact1", '0788111111'),
                                    self.create_contact("Contact2", '0788222222'),
                                    self.create_contact("Contact3", '0788333333'),
                                    self.create_contact("Contact4", '0788444444'),
                                    self.create_contact("Contact5", '0788555555'),
                                    self.create_contact("Contact6", '0788666666', is_test=True))

        run_flow(c1, "1 better place")
        run_flow(c2, "the great coffee")
        run_flow(c3, "1 cup of black tea")
        run_flow(c4, "awesome than this encore")
        run_flow(c5, "from an awesome place in kigali")
        run_flow(c6, "awesome coffee")

        random = RuleSet.objects.get(flow=flow, label="Random")

        result = Value.get_value_summary(ruleset=random)[0]
        self.assertEqual(10, len(result['categories']))
        self.assertTrue(result['open_ended'])
        self.assertResult(result, 0, "awesome", 2)
        self.assertResult(result, 1, "place", 2)
        self.assertResult(result, 2, "better", 1)
        self.assertResult(result, 3, "black", 1)
        self.assertResult(result, 4, "coffee", 1)
        self.assertResult(result, 5, "cup", 1)
        self.assertResult(result, 6, "encore", 1)
        self.assertResult(result, 7, "great", 1)
        self.assertResult(result, 8, "kigali", 1)
        self.assertResult(result, 9, "tea", 1)

        # add French to org languages
        Language.create(self.org, self.admin, 'French', 'fra')

        # make sure we cleared the cache
        Value.invalidate_cache(ruleset=random)

        # encore is a french stop word and should not be included this time
        result = Value.get_value_summary(ruleset=random)[0]
        self.assertEqual(9, len(result['categories']))
        self.assertTrue(result['open_ended'])
        self.assertResult(result, 0, "awesome", 2)
        self.assertResult(result, 1, "place", 2)
        self.assertResult(result, 2, "better", 1)
        self.assertResult(result, 3, "black", 1)
        self.assertResult(result, 4, "coffee", 1)
        self.assertResult(result, 5, "cup", 1)
        self.assertResult(result, 6, "great", 1)
        self.assertResult(result, 7, "kigali", 1)
        self.assertResult(result, 8, "tea", 1)
