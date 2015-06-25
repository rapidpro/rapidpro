import json
from django.core.urlresolvers import reverse
from temba.tests import TembaTest

class Locationtest(TembaTest):

    def test_boundaries(self):
        self.login(self.admin)

        # clear our country on our org
        self.org.country = None
        self.org.save()

        # get the aliases for our user org
        response = self.client.get(reverse('locations.adminboundary_alias'))

        # should be a redirect to our org home
        self.assertRedirect(response, reverse('orgs.org_home'))

        # now set it to rwanda
        self.org.country = self.country
        self.org.save()

        # our country is set to rwanda, we should get it as the main object
        response = self.client.get(reverse('locations.adminboundary_alias'))
        self.assertEquals(self.country, response.context['object'])

        # ok, now get the geometry for rwanda
        response = self.client.get(reverse('locations.adminboundary_geometry', args=[self.country.osm_id]))

        # should be json
        response_json = json.loads(response.content)

        # should have features in it
        self.assertTrue('features' in response_json)

        # should have our two top level states
        self.assertEquals(2, len(response_json['features']))

        # now get it for one of the sub areas
        response = self.client.get(reverse('locations.adminboundary_geometry', args=[self.district1.osm_id]))
        response_json = json.loads(response.content)

        # should have features in it
        self.assertTrue('features' in response_json)

        # should have our single district in it
        self.assertEquals(1, len(response_json['features']))

        # now grab our aliases
        response = self.client.get(reverse('locations.adminboundary_boundaries', args=[self.country.osm_id]))
        response_json = json.loads(response.content)

        # should just be kigali, without any aliases
        self.assertEquals(2, len(response_json))
        self.assertEquals("Eastern Province", response_json[0]['name'])
        self.assertEquals("Kigali City", response_json[1]['name'])
        self.assertEquals('', response_json[1]['aliases'])

        # update our alias for kigali
        response = self.client.post(reverse('locations.adminboundary_boundaries', args=[self.country.osm_id]),
                                    json.dumps([dict(osm_id=self.state1.osm_id, aliases="kigs\nkig")]),
                                    content_type='application/json')

        self.assertEquals(200, response.status_code)

        # fetch our aliases again
        response = self.client.get(reverse('locations.adminboundary_boundaries', args=[self.country.osm_id]))
        response_json = json.loads(response.content)

        # now have kigs as an alias
        self.assertEquals("Kigali", response_json[1]['name'])
        self.assertEquals(['kigs', 'kig'], response_json[1]['aliases'])






