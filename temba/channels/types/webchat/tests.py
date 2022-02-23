from django.urls import reverse

from temba.channels.models import Channel
from temba.tests import TembaTest


class WebChatTypeTest(TembaTest):
    def test_claim(self):
        url = reverse("channels.types.webchat.claim")
        web_chat_name = "Test WebChat"
        self.login(self.admin)

        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        # try to claim a channel
        response = self.client.get(url)
        post_data = response.context["form"].initial
        post_data["channel_name"] = web_chat_name

        response = self.client.post(url, post_data)

        channel = Channel.objects.get(channel_type="WCH")
        self.assertEqual(channel.name, web_chat_name)

        update_url = reverse("channels.channel_update", args=[channel.id])
        self.assertRedirect(response, update_url)

    def test_web_chat_render_download(self):
        file_name = "steve.marten.jpg"
        media_url = "https://example.com"

        url = reverse("webchat_render_download")
        self.login(self.admin)

        response = self.client.get(url)
        self.assertEqual(response.status_code, 500)
        response = self.client.get(url, data=dict(url=f"{media_url}/${file_name}"))
        self.assertEqual(response.status_code, 404)

        response = self.client.get(url, data=dict(url=media_url))
        self.assertEqual(response.status_code, 200)
