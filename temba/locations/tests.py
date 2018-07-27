# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import os
import json
import responses
import shutil
import tempfile

from django.core.management import call_command
from django.core.urlresolvers import reverse
from temba.tests import TembaTest
from .models import AdminBoundary


class LocationTest(TembaTest):

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
        self.assertEqual(self.country, response.context['object'])

        # ok, now get the geometry for rwanda
        response = self.client.get(
            reverse('locations.adminboundary_geometry', args=[self.country.osm_id]))

        # should be json
        response_json = response.json()

        # should have features in it
        self.assertIn('features', response_json)

        # should have our two top level states
        self.assertEqual(2, len(response_json['features']))

        # now get it for one of the sub areas
        response = self.client.get(
            reverse('locations.adminboundary_geometry', args=[self.district1.osm_id]))
        response_json = response.json()

        # should have features in it
        self.assertIn('features', response_json)

        # should have our single district in it
        self.assertEqual(1, len(response_json['features']))

        # now grab our aliases
        response = self.client.get(
            reverse('locations.adminboundary_boundaries', args=[self.country.osm_id]))
        response_json = response.json()

        # should just be kigali, without any aliases
        self.assertEqual(2, len(response_json))
        self.assertEqual("Eastern Province", response_json[0]['name'])
        self.assertEqual("Kigali City", response_json[1]['name'])
        self.assertEqual('', response_json[1]['aliases'])

        # update our alias for kigali
        response = self.client.post(reverse('locations.adminboundary_boundaries', args=[self.country.osm_id]),
                                    json.dumps(
                                        [dict(osm_id=self.state1.osm_id, aliases="kigs\nkig")]),
                                    content_type='application/json')

        self.assertEqual(200, response.status_code)

        # fetch our aliases again
        response = self.client.get(
            reverse('locations.adminboundary_boundaries', args=[self.country.osm_id]))
        response_json = response.json()

        # now have kigs as an alias
        self.assertEqual("Kigali City", response_json[1]['name'])
        self.assertEqual('kig\nkigs', response_json[1]['aliases'])

        # test nested admin level aliases update
        geo_data = [
            dict(
                osm_id=self.state2.osm_id,
                aliases="Eastern P",
                children=[
                    dict(
                        osm_id=self.district1.osm_id,
                        aliases="Gatsibo",
                        children=[
                            dict(
                                osm_id=self.ward1.osm_id,
                                aliases="Kageyo Gat"
                            )
                        ]
                    )
                ]
            )
        ]
        response = self.client.post(reverse('locations.adminboundary_boundaries', args=[self.country.osm_id]),
                                    json.dumps(geo_data), content_type='application/json')

        self.assertEqual(200, response.status_code)

        # exact match
        boundary = self.org.find_boundary_by_name('kigali city', AdminBoundary.LEVEL_STATE, self.country)
        self.assertEqual(len(boundary), 1)
        self.assertEqual(boundary[0], self.state1)

        # try to find the location by alias
        boundary = self.org.find_boundary_by_name('kigs', AdminBoundary.LEVEL_STATE, self.country)
        self.assertEqual(len(boundary), 1)
        self.assertEqual(boundary[0], self.state1)

        # also try with no parent
        boundary = self.org.find_boundary_by_name('kigs', AdminBoundary.LEVEL_STATE, None)
        self.assertEqual(len(boundary), 1)
        self.assertEqual(boundary[0], self.state1)

        # test no match
        boundary = self.org.find_boundary_by_name('foobar', AdminBoundary.LEVEL_STATE, None)
        self.assertFalse(boundary)

        # fetch aliases again
        response = self.client.get(
            reverse('locations.adminboundary_boundaries', args=[self.country.osm_id]))
        response_json = response.json()
        self.assertEqual(response_json[0].get('name'), self.state2.name)
        self.assertEqual(response_json[0].get('aliases'), 'Eastern P')
        self.assertIn('Kageyo Gat', response_json[0].get('match'))

        # trigger wrong request data using bad json
        response = self.client.post(reverse('locations.adminboundary_boundaries', args=[self.country.osm_id]),
                                    """{"data":"foo \r\n bar"}""",
                                    content_type='application/json')

        response_json = response.json()
        self.assertEqual(400, response.status_code)
        self.assertEqual(response_json.get('status'), 'error')

        # Get geometry of admin boundary without sub-levels, should return one feature
        response = self.client.get(
            reverse('locations.adminboundary_geometry', args=[self.ward3.osm_id]))
        self.assertEqual(200, response.status_code)
        response_json = response.json()
        self.assertEqual(len(response_json.get('features')), 1)


class DownloadGeoJsonTest(TembaTest):

    def setUp(self):
        responses.add(
            responses.GET,
            'https://api.github.com/repos/nyaruka/posm-extracts/git/trees/master',
            body=json.dumps({'tree': [{"path": "geojson", "sha": "the-sha"}]}),
            content_type='application/json')
        responses.add(
            responses.GET,
            'https://api.github.com/repos/nyaruka/posm-extracts/git/trees/the-sha',
            body=json.dumps({'tree': [{"path": "R12345_simplified.json"},
                                      {"path": "R45678_simplified.json"}]}),
            content_type='application/json')
        responses.add(
            responses.GET,
            'https://raw.githubusercontent.com/nyaruka/posm-extracts/master/geojson/R12345_simplified.json',
            body='the-relation-json', content_type='application/json')
        self.testdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.testdir)

    @responses.activate
    def test_download_geojson(self):
        destination_dir = os.path.join(self.testdir, 'geojson')
        good_path = os.path.join(destination_dir, 'R12345_simplified.json')
        bad_path = os.path.join(destination_dir, 'R45678_simplified.json')
        call_command('download_geojson', '12345', '--dir', destination_dir)
        self.assertFalse(os.path.exists(bad_path))
        self.assertTrue(os.path.exists(good_path))
        with open(good_path, 'r') as fp:
            self.assertEqual(fp.read(), 'the-relation-json')
