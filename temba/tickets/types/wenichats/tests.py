from temba.tests import MockResponse, TembaTest
from temba.tickets.models import Ticketer
from .type import WeniChatsType
from django.urls import reverse
from unittest.mock import patch
from temba.utils import json

from pprint import pprint

class WeniChatsTypeTest(TembaTest):
  def test_is_available_to(self):
    self.assertTrue(WeniChatsType().is_available_to(self.admin))

class WeniChatsMixin(TembaTest):
  def setUp(self):
    super().setUp()
    self.connect_url  = reverse("tickets.types.wenichats.connect")
  
class WeniChatsViewTest(WeniChatsMixin):
  @patch("requests.get")
  def test_connect(self, mock_get):
    self.client.force_login(self.admin)

    data = {
      "sector_uuid": "d3cae43d-cf25-4892-bfa6-0f24a856cfb8",
      "project_auth": "bb0682cd-5ed6-4c3d-851f-b2f0c1952f81"
    }

    mock_get.return_value = MockResponse(
      200,
      json.dumps(
        {
          "count": 3,
          "next": "",
          "previous": "",
          "results": [
            {
              "uuid": "21aecf8c-0c73-4059-ba82-4343e0cc627c",
              "name": "Fluxos"
            },
            {
              "uuid": "4f88b656-194d-4a83-a166-5d84ba825b8d",
              "name": "Inteligencia"
            },
            {
              "uuid": "d3cae43d-cf25-4892-bfa6-0f24a856cfb8",
              "name": "Contas"
            }
          ]
        }
      )
    )

    response = self.client.post(self.connect_url, data=data)
    self.assertEqual(response.status_code, 302)

    ticketer = Ticketer.objects.order_by("id").last()
    self.assertEqual("Contas", ticketer.name)

    self.assertRedirect(response, reverse("tickets.ticket_list"))
