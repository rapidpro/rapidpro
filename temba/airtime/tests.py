# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.core.urlresolvers import reverse
from mock import patch
from temba.airtime.models import AirtimeTransfer
from temba.flows.models import RuleSet
from temba.tests import TembaTest, MockResponse


class AirtimeEventTest(TembaTest):
    def setUp(self):
        super(AirtimeEventTest, self).setUp()

        self.contact = self.create_contact('Ben Haggerty', '+12065552020')
        self.org.connect_transferto('mylogin', 'api_token', self.admin)
        self.airtime = AirtimeTransfer.objects.create(org=self.org, recipient='+12065552020', amount='100',
                                                      contact=self.contact, created_by=self.admin, modified_by=self.admin)

    def test_parse_transferto_response(self):
        self.assertEqual(AirtimeTransfer.parse_transferto_response(""), dict())

        self.assertEqual(AirtimeTransfer.parse_transferto_response("foo"), dict())

        self.assertEqual(AirtimeTransfer.parse_transferto_response("foo\r\nbar"), dict())

        self.assertEqual(AirtimeTransfer.parse_transferto_response("foo=allo\r\nbar"),
                         dict(foo='allo'))

        self.assertEqual(AirtimeTransfer.parse_transferto_response("foo=allo\r\nbar=1,2,3\r\n"),
                         dict(foo='allo', bar=['1', '2', '3']))

    @patch('requests.post')
    def test_post_transferto_api_response(self, mock_post):
        mock_post.return_value = MockResponse(200, "foo=allo\r\nbar=1,2,3\r\n")

        # Send airtime disabled should raise exception
        with self.assertRaises(Exception):
            AirtimeTransfer.post_transferto_api_response('login_acc', 'token', action='ping')

        with self.settings(SEND_AIRTIME=True):
            model_obj_data = self.airtime.data
            model_obj_response = self.airtime.response

            response = AirtimeTransfer.post_transferto_api_response('login_acc', 'token', action='ping')
            self.assertContains(response, "foo=allo\r\nbar=1,2,3\r\n")

            self.assertEqual(mock_post.call_count, 1)
            self.assertEqual('https://airtime.transferto.com/cgi-bin/shop/topup', mock_post.call_args_list[0][0][0])
            mock_args = mock_post.call_args_list[0][0][1]
            self.assertIn('action', mock_args.keys())
            self.assertIn('login', mock_args.keys())
            self.assertIn('key', mock_args.keys())
            self.assertIn('md5', mock_args.keys())

            self.assertIn('ping', mock_args.values())
            self.assertIn('login_acc', mock_args.values())

            self.airtime.refresh_from_db()
            # model not changed since not passed in args
            self.assertEqual(self.airtime.data, model_obj_data)
            self.assertEqual(self.airtime.response, model_obj_response)
            mock_post.reset_mock()

            response = AirtimeTransfer.post_transferto_api_response('login_acc', 'token', airtime_obj=self.airtime,
                                                                    action='ping')
            self.assertContains(response, "foo=allo\r\nbar=1,2,3\r\n")
            self.assertEqual(mock_post.call_count, 1)
            self.assertEqual('https://airtime.transferto.com/cgi-bin/shop/topup', mock_post.call_args_list[0][0][0])
            mock_args = mock_post.call_args_list[0][0][1]
            self.assertIn('action', mock_args.keys())
            self.assertIn('login', mock_args.keys())
            self.assertIn('key', mock_args.keys())
            self.assertIn('md5', mock_args.keys())

            self.assertIn('ping', mock_args.values())
            self.assertIn('login_acc', mock_args.values())

            self.airtime.refresh_from_db()
            # model changed since it is passed in args
            self.assertNotEqual(self.airtime.data, model_obj_data)
            self.assertNotEqual(self.airtime.response, model_obj_response)
            mock_post.reset_mock()

    @patch('temba.airtime.models.AirtimeTransfer.post_transferto_api_response')
    def test_get_transferto_response(self, mock_post_transferto):
        mock_post_transferto.return_value = MockResponse(200, "foo=allo\r\nbar=1,2,3\r\n")

        with self.settings(SEND_AIRTIME=True):
            response = self.airtime.get_transferto_response(action='command')
            self.assertContains(response, "foo=allo\r\nbar=1,2,3\r\n")

            mock_post_transferto.assert_called_once_with('mylogin', 'api_token', airtime_obj=self.airtime,
                                                         action='command')

    @patch('temba.airtime.models.AirtimeTransfer.post_transferto_api_response')
    @patch('temba.airtime.models.AirtimeTransfer.get_transferto_response')
    def test_airtime_trigger_event(self, mock_response, mock_post_api_response):
        flow = self.get_flow('airtime')
        ruleset = RuleSet.objects.get(flow=flow)
        org = flow.org

        # disconnect transferTo account
        org.remove_transferto_account(self.admin)
        mock_response.side_effect = [MockResponse(200, "error_code=0\r\nerror_txt=\r\ncountry=United States\r\n"
                                                       "product_list=0.25,0.5,1,1.5\r\n"
                                                       "local_info_value_list=5,10,20,30\r\n"),
                                     MockResponse(200, "error_code=0\r\nerror_txt=\r\nreserved_id=234\r\n"),
                                     MockResponse(200, "error_code=0\r\nerror_txt=\r\n")]

        mock_post_api_response.return_value = MockResponse(200, "error_code=0\r\ncurrency=USD\r\n")

        airtime = AirtimeTransfer.trigger_airtime_event(org, ruleset, self.contact, None)
        self.assertEqual(airtime.status, AirtimeTransfer.FAILED)
        self.assertEqual(airtime.contact, self.contact)
        self.assertEqual(airtime.message, "Error transferring airtime: No transferTo Account connected to "
                                          "this organization")

        # we never call TransferTo API if no account is connected
        self.assertEqual(mock_response.call_count, 0)
        mock_response.reset_mock()

        # now have an account connected
        org.connect_transferto('mylogin', 'api_token', self.admin)

        mock_response.side_effect = [MockResponse(200, "error_code=0\r\nerror_txt=\r\ncountry=United States\r\n"
                                                       "product_list=0.25,0.5,1,1.5\r\n"
                                                       "local_info_value_list=5,10,20,30\r\n"),
                                     MockResponse(200, "error_code=0\r\nerror_txt=\r\nreserved_id=234\r\n"),
                                     MockResponse(200, "error_code=0\r\nerror_txt=\r\n")]

        airtime = AirtimeTransfer.trigger_airtime_event(org, ruleset, self.contact, None)
        self.assertEqual(airtime.status, AirtimeTransfer.SUCCESS)
        self.assertEqual(airtime.contact, self.contact)
        self.assertEqual(airtime.message, "Airtime Transferred Successfully")
        self.assertEqual(mock_response.call_count, 3)
        self.assertTrue(({'action': 'msisdn_info', 'currency': 'USD', 'destination_msisdn': '+12065552020', 'delivered_amount_info': '1'},) in mock_response.call_args_list)
        self.assertTrue(({'action': 'reserve_id'},) in mock_response.call_args_list)
        self.assertTrue(({'action': 'topup', 'reserved_id': '234', 'msisdn': '',
                          'destination_msisdn': '+12065552020', 'currency': 'USD',
                          'product': '0.5'},) in mock_response.call_args_list)
        mock_response.reset_mock()

        mock_response.side_effect = [MockResponse(200, "error_code=0\r\nerror_txt=\r\ncountry=Rwanda\r\n"
                                                       "product_list=0.25,0.5,1,1.5\r\n"
                                                       "local_info_value_list=5,10,20,30\r\n"),
                                     MockResponse(200, "error_code=0\r\nerror_txt=\r\nreserved_id=234\r\n"),
                                     MockResponse(200, "error_code=0\r\nerror_txt=\r\n")]

        airtime = AirtimeTransfer.trigger_airtime_event(org, ruleset, self.contact, None)
        self.assertEqual(airtime.status, AirtimeTransfer.FAILED)
        self.assertEqual(airtime.message, "Error transferring airtime: Failed by invalid amount "
                                          "configuration or missing amount configuration for Rwanda")
        self.assertTrue(({'action': 'msisdn_info', 'currency': 'USD',
                          'destination_msisdn': '+12065552020', 'delivered_amount_info': '1'},) in mock_response.call_args_list)
        self.assertEqual(mock_response.call_count, 1)
        mock_response.reset_mock()

        # first error code not 0
        mock_response.side_effect = [MockResponse(200, "error_code=1\r\nerror_txt=\r\ncountry=United States\r\n"
                                                       "product_list=0.25,0.5,1,1.5\r\n"
                                                       "local_info_value_list=5,10,20,30\r\n"),
                                     MockResponse(200, "error_code=0\r\nerror_txt=\r\nreserved_id=234\r\n"),
                                     MockResponse(200, "error_code=0\r\nerror_txt=\r\n")]

        airtime = AirtimeTransfer.trigger_airtime_event(org, ruleset, self.contact, None)
        self.assertEqual(airtime.status, AirtimeTransfer.FAILED)
        self.assertTrue(({'action': 'msisdn_info', 'currency': 'USD',
                          'destination_msisdn': '+12065552020', 'delivered_amount_info': '1'},) in mock_response.call_args_list)
        self.assertEqual(mock_response.call_count, 1)
        mock_response.reset_mock()

        # second error code not 0
        mock_response.side_effect = [MockResponse(200, "error_code=0\r\nerror_txt=\r\ncountry=United States\r\n"
                                                       "product_list=0.25,0.5,1,1.5\r\n"
                                                       "local_info_value_list=5,10,20,30\r\n"),
                                     MockResponse(200, "error_code=1\r\nerror_txt=\r\nreserved_id=234\r\n"),
                                     MockResponse(200, "error_code=0\r\nerror_txt=\r\n")]

        airtime = AirtimeTransfer.trigger_airtime_event(org, ruleset, self.contact, None)
        self.assertEqual(airtime.status, AirtimeTransfer.FAILED)

        self.assertTrue(({'action': 'msisdn_info', 'currency': 'USD',
                          'destination_msisdn': '+12065552020', 'delivered_amount_info': '1'},) in mock_response.call_args_list)
        self.assertTrue(({'action': 'reserve_id'},) in mock_response.call_args_list)
        self.assertEqual(mock_response.call_count, 2)
        mock_response.reset_mock()

        # third error code not 0
        mock_response.side_effect = [MockResponse(200, "error_code=0\r\nerror_txt=\r\ncountry=United States\r\n"
                                                       "product_list=0.25,0.5,1,1.5\r\n"
                                                       "local_info_value_list=5,10,20,30\r\n"),
                                     MockResponse(200, "error_code=0\r\nerror_txt=\r\nreserved_id=234\r\n"),
                                     MockResponse(200, "error_code=1\r\nerror_txt=\r\n")]

        airtime = AirtimeTransfer.trigger_airtime_event(org, ruleset, self.contact, None)
        self.assertEqual(airtime.status, AirtimeTransfer.FAILED)
        self.assertTrue(({'action': 'msisdn_info', 'currency': 'USD',
                          'destination_msisdn': '+12065552020', 'delivered_amount_info': '1'},) in mock_response.call_args_list)
        self.assertTrue(({'action': 'reserve_id'},) in mock_response.call_args_list)
        self.assertTrue(({'action': 'topup', 'reserved_id': '234', 'msisdn': '',
                          'destination_msisdn': '+12065552020', 'currency': 'USD',
                          'product': '0.5'},) in mock_response.call_args_list)
        self.assertEqual(mock_response.call_count, 3)
        mock_response.reset_mock()

        # when we need to include skuid
        mock_response.side_effect = [MockResponse(200, "error_code=0\r\nerror_txt=\r\ncountry=United States\r\n"
                                                       "product_list=0.25,0.5,1,1.5\r\n"
                                                       "skuid_list=1625,9805,4561,9715\r\n"
                                                       "local_info_value_list=5,10,20,30\r\n"),
                                     MockResponse(200, "error_code=0\r\nerror_txt=\r\nreserved_id=234\r\n"),
                                     MockResponse(200, "error_code=0\r\nerror_txt=\r\n")]

        airtime = AirtimeTransfer.trigger_airtime_event(org, ruleset, self.contact, None)
        self.assertEqual(airtime.status, AirtimeTransfer.SUCCESS)
        self.assertEqual(airtime.contact, self.contact)
        self.assertEqual(airtime.message, "Airtime Transferred Successfully")
        self.assertEqual(mock_response.call_count, 3)
        self.assertTrue(({'action': 'msisdn_info', 'currency': 'USD', 'destination_msisdn': '+12065552020',
                          'delivered_amount_info': '1'},) in mock_response.call_args_list)
        self.assertTrue(({'action': 'reserve_id'},) in mock_response.call_args_list)
        self.assertTrue(({'action': 'topup', 'reserved_id': '234', 'msisdn': '', 'skuid': '9805',
                          'destination_msisdn': '+12065552020', 'currency': 'USD',
                          'product': '0.5'},) in mock_response.call_args_list)
        mock_response.reset_mock()

        # when product_list, skuid_list, ... are not parsed as lists in the case of a single value

        mock_response.side_effect = [MockResponse(200, "error_code=0\r\nerror_txt=\r\ncountry=United States\r\n"
                                                       "product_list=0.5\r\n"
                                                       "skuid_list=5505\r\n"
                                                       "local_info_value_list=10\r\n"),
                                     MockResponse(200, "error_code=0\r\nerror_txt=\r\nreserved_id=234\r\n"),
                                     MockResponse(200, "error_code=0\r\nerror_txt=\r\n")]

        airtime = AirtimeTransfer.trigger_airtime_event(org, ruleset, self.contact, None)
        self.assertEqual(airtime.status, AirtimeTransfer.SUCCESS)
        self.assertEqual(airtime.contact, self.contact)
        self.assertEqual(airtime.message, "Airtime Transferred Successfully")
        self.assertEqual(mock_response.call_count, 3)
        self.assertTrue(({'action': 'msisdn_info', 'currency': 'USD', 'destination_msisdn': '+12065552020',
                          'delivered_amount_info': '1'},) in mock_response.call_args_list)
        self.assertTrue(({'action': 'reserve_id'},) in mock_response.call_args_list)
        self.assertTrue(({'action': 'topup', 'reserved_id': '234', 'msisdn': '', 'skuid': '5505',
                          'destination_msisdn': '+12065552020', 'currency': 'USD',
                          'product': '0.5'},) in mock_response.call_args_list)
        mock_response.reset_mock()

        # for open range only, no product_list, no skuid_list,
        # just a skuid we just have to pass the amount as denomination
        mock_response.side_effect = [MockResponse(200, "error_code=0\r\nerror_txt=\r\ncountry=United States\r\n"
                                                       "open_range_minimum_amount_local_currency=5\r\n"
                                                       "open_range_maximum_amount_local_currency=100\r\n"
                                                       "open_range_minimum_amount_requested_currency=0.25\r\n"
                                                       "open_range_maximum_amount_requested_currency=5\r\n"
                                                       "skuid=9940\r\n"),
                                     MockResponse(200, "error_code=0\r\nerror_txt=\r\nreserved_id=234\r\n"),
                                     MockResponse(200, "error_code=0\r\nerror_txt=\r\n")]

        airtime = AirtimeTransfer.trigger_airtime_event(org, ruleset, self.contact, None)
        self.assertEqual(airtime.status, AirtimeTransfer.SUCCESS)
        self.assertEqual(airtime.contact, self.contact)
        self.assertEqual(airtime.message, "Airtime Transferred Successfully")
        self.assertEqual(mock_response.call_count, 3)
        self.assertTrue(({'action': 'msisdn_info', 'currency': 'USD', 'destination_msisdn': '+12065552020',
                          'delivered_amount_info': '1'},) in mock_response.call_args_list)
        self.assertTrue(({'action': 'reserve_id'},) in mock_response.call_args_list)
        self.assertTrue(({'action': 'topup', 'reserved_id': '234', 'msisdn': '', 'skuid': '9940',
                          'destination_msisdn': '+12065552020', 'currency': 'USD',
                          'product': '0.5'},) in mock_response.call_args_list)
        mock_response.reset_mock()

    def test_list(self):
        list_url = reverse('airtime.airtimetransfer_list')

        self.login(self.user)
        response = self.client.get(list_url)
        self.assertRedirect(response, '/users/login/')

        self.login(self.editor)
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.airtime in response.context['object_list'])

        self.login(self.admin)
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.airtime in response.context['object_list'])

    def test_read(self):
        read_url = reverse('airtime.airtimetransfer_read', args=[self.airtime.pk])

        self.login(self.user)
        response = self.client.get(read_url)
        self.assertRedirect(response, '/users/login/')

        self.login(self.editor)
        response = self.client.get(read_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.airtime.pk, response.context['object'].pk)

        self.login(self.admin)
        response = self.client.get(read_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.airtime.pk, response.context['object'].pk)
