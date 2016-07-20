from django.core.urlresolvers import reverse
from mock import patch
from temba.airtime.models import Airtime
from temba.tests import TembaTest, MockResponse


class AirtimeEventTest(TembaTest):
    def setUp(self):
        super(AirtimeEventTest, self).setUp()

        self.contact = self.create_contact('Bob', number='+250788123123')
        self.org.connect_transferto('mylogin', 'api_token', self.admin)
        self.airtime = Airtime.objects.create(org=self.org, recipient='+250788123123', amount='100',
                                              contact=self.contact, created_by=self.admin, modified_by=self.admin)

    def test_parse_transferto_response(self):
        self.assertEqual(Airtime.parse_transferto_response(""), dict())

        self.assertEqual(Airtime.parse_transferto_response("foo"), dict())

        self.assertEqual(Airtime.parse_transferto_response("foo\r\nbar"), dict())

        self.assertEqual(Airtime.parse_transferto_response("foo=allo\r\nbar"),
                         dict(foo='allo'))

        self.assertEqual(Airtime.parse_transferto_response("foo=allo\r\nbar=1,2,3\r\n"),
                         dict(foo='allo', bar=['1', '2', '3']))

    @patch('temba.airtime.models.Airtime.post_transferto_api_response')
    def test_get_transferto_response_json(self, mock_post_transferto):
        mock_post_transferto.return_value = MockResponse(200, "foo=allo\r\nbar=1,2,3\r\n")

        with self.settings(SEND_AIRTIME=True):
            response = self.airtime.get_transferto_response_json(action='command')
            self.assertEqual(200, response.status_code)
            self.assertEqual(response.content, "foo=allo\r\nbar=1,2,3\r\n")

            mock_post_transferto.assert_called_once_with('mylogin', 'api_token', airtime_obj=self.airtime,
                                                         action='command')

    def test_list(self):
        list_url = reverse('airtime.airtime_list')

        self.login(self.user)
        response = self.client.get(list_url)
        self.assertRedirect(response, '/users/login/')

        self.login(self.editor)
        response = self.client.get(list_url)
        self.assertRedirect(response, '/users/login/')

        self.login(self.admin)
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.airtime in response.context['object_list'])

    def test_read(self):
        read_url = reverse('airtime.airtime_read', args=[self.airtime.pk])

        self.login(self.user)
        response = self.client.get(read_url)
        self.assertRedirect(response, '/users/login/')

        self.login(self.editor)
        response = self.client.get(read_url)
        self.assertRedirect(response, '/users/login/')

        self.login(self.admin)
        response = self.client.get(read_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.airtime.pk, response.context['object'].pk)
