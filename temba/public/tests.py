from unittest.mock import MagicMock

from smartmin.tests import _CRUDLTest

from django.contrib.auth.models import User
from django.core.files import File
from django.urls import reverse

from temba.apks.models import Apk
from temba.tests import TembaTest, TembaTestMixin

from .models import Lead, Video
from .views import VideoCRUDL


class PublicTest(TembaTest):
    def test_index(self):
        home_url = reverse("public.public_index")
        response = self.client.get(home_url, follow=True)
        self.assertEqual(response.request["PATH_INFO"], "/")

        response = self.client.get(home_url + "?errors=&foo", follow=True)
        self.assertEqual(response.request["PATH_INFO"], "/")
        self.assertTrue(response.context["errors"])
        self.assertFalse("error_msg" in response.context)

        # try to create a lead from the homepage
        lead_create_url = reverse("public.lead_create")
        post_data = dict()
        response = self.client.post(lead_create_url, post_data, follow=True)
        self.assertEqual(response.request["PATH_INFO"], "/")
        self.assertTrue(response.context["errors"])
        self.assertEqual(response.context["error_msg"], "This field is required.")

        post_data["email"] = "wrong_email_format"
        response = self.client.post(lead_create_url, post_data, follow=True)
        self.assertEqual(response.request["PATH_INFO"], "/")
        self.assertTrue(response.context["errors"])
        self.assertEqual(response.context["error_msg"], "Enter a valid email address.")

        post_data["email"] = "immortal@temba.com"
        response = self.client.post(lead_create_url, post_data, follow=True)
        self.assertEqual(response.request["PATH_INFO"], reverse("orgs.org_signup"))

    def test_android(self):
        android_url = reverse("public.public_android")
        response = self.client.get(android_url, follow=True)
        self.assertEqual(404, response.status_code)

        apk_file_mock = MagicMock(spec=File)
        apk_file_mock.name = "relayer.apk"
        apk = Apk.objects.create(apk_type="R", version="1.9.8", description="* better syncing", apk_file=apk_file_mock)

        android_url = reverse("public.public_android")
        response = self.client.get(android_url, follow=True)
        self.assertEqual(response.request["PATH_INFO"], apk.apk_file.url)

        apk_pack_file_mock = MagicMock(spec=File)
        apk_pack_file_mock.name = "pack.apk"
        pack_apk = Apk.objects.create(
            apk_type="M", version="1.9.8", pack=1, description="* latest pack", apk_file=apk_pack_file_mock
        )

        response = self.client.get(f"{android_url}?v=1.9.8&pack=1", follow=True)
        self.assertEqual(response.request["PATH_INFO"], pack_apk.apk_file.url)

    def test_welcome(self):
        welcome_url = reverse("public.public_welcome")
        response = self.client.get(welcome_url, follow=True)
        self.assertIn("next", response.request["QUERY_STRING"])
        self.assertEqual(response.request["PATH_INFO"], reverse("users.user_login"))

        self.login(self.user)
        response = self.client.get(welcome_url, follow=True)
        self.assertEqual(response.request["PATH_INFO"], reverse("public.public_welcome"))

    def test_leads(self):
        create_url = reverse("public.lead_create")

        post_data = dict()
        post_data["email"] = "eugene@temba.com"
        response = self.client.post(create_url, post_data, follow=True)
        self.assertEqual(len(Lead.objects.all()), 1)

        # create mailing list with the same email again, we actually allow dupes now
        post_data["email"] = "eugene@temba.com"
        response = self.client.post(create_url, post_data, follow=True)
        self.assertEqual(len(Lead.objects.all()), 2)

        # invalid email
        post_data["email"] = "asdfasdf"
        response = self.client.post(create_url, post_data, follow=True)
        self.assertEqual(response.request["PATH_INFO"], "/")
        self.assertEqual(len(Lead.objects.all()), 2)

    def test_demo_coupon(self):
        coupon_url = reverse("demo.generate_coupon")
        response = self.client.get(coupon_url, follow=True)
        self.assertEqual(response.request["PATH_INFO"], coupon_url)
        self.assertContains(response, "coupon")

    def test_demo_status(self):
        status_url = reverse("demo.order_status")
        response = self.client.get(status_url, follow=True)
        self.assertEqual(response.request["PATH_INFO"], status_url)
        self.assertContains(response, "Invalid")

        response = self.client.get("%s?text=somethinginvalid" % status_url)
        self.assertEqual(response.request["PATH_INFO"], status_url)
        self.assertContains(response, "Invalid")

        response = self.client.get("%s?text=cu001" % status_url)
        self.assertEqual(response.request["PATH_INFO"], status_url)
        self.assertContains(response, "Shipped")

        response = self.client.get("%s?text=cu002" % status_url)
        self.assertEqual(response.request["PATH_INFO"], status_url)
        self.assertContains(response, "Pending")

        response = self.client.get("%s?text=cu003" % status_url)
        self.assertEqual(response.request["PATH_INFO"], status_url)
        self.assertContains(response, "Cancelled")

        response = self.client.post(status_url, {}, content_type="application/json", follow=True)
        self.assertEqual(response.request["PATH_INFO"], status_url)
        self.assertContains(response, "Invalid")

        response = self.client.post(status_url, dict(text="somethinginvalid"), content_type="application/json")
        self.assertEqual(response.request["PATH_INFO"], status_url)
        self.assertContains(response, "Invalid")

        response = self.client.post(status_url, dict(input=dict(text="CU001")), content_type="application/json")
        self.assertEqual(response.request["PATH_INFO"], status_url)
        self.assertContains(response, "Shipped")

        response = self.client.post(status_url, dict(input=dict(text="CU002")), content_type="application/json")
        self.assertEqual(response.request["PATH_INFO"], status_url)
        self.assertContains(response, "Pending")

        response = self.client.post(status_url, dict(input=dict(text="CU003")), content_type="application/json")
        self.assertEqual(response.request["PATH_INFO"], status_url)
        self.assertContains(response, "Cancelled")

    def test_templatetags(self):
        from .templatetags.public import gear_link_classes

        link = dict()
        link["posterize"] = True
        self.assertTrue("posterize", gear_link_classes(link))
        link["js_class"] = "alright"
        self.assertTrue("posterize alright", gear_link_classes(link))
        link["style"] = "pull-right"
        self.assertTrue("posterize alright pull-right", gear_link_classes(link, True))
        link["modal"] = True
        self.assertTrue("posterize alright pull-right gear-modal", gear_link_classes(link, True))
        link["delete"] = True
        self.assertTrue("posterize alright pull-right gear-modal gear-delete", gear_link_classes(link, True))

    def test_sitemaps(self):
        sitemap_url = reverse("public.sitemaps")

        # number of fixed items (i.e. not videos, differs between configurations)
        response = self.client.get(sitemap_url)

        # but first item is always home page
        self.assertEqual(
            response.context["urlset"][0],
            {
                "priority": "0.5",
                "item": "public.public_index",
                "lastmod": None,
                "changefreq": "daily",
                "location": "http://example.com/",
            },
        )

        num_fixed_items = len(response.context["urlset"])

        # adding a video will dynamically add a new item
        Video.objects.create(
            name="Item14",
            summary="Unicorn",
            description="Video of unicorns",
            vimeo_id="1234",
            order=0,
            created_by=self.superuser,
            modified_by=self.superuser,
        )

        response = self.client.get(sitemap_url)
        self.assertEqual(len(response.context["urlset"]), num_fixed_items + 1)


class VideoCRUDLTest(TembaTestMixin, _CRUDLTest):
    def setUp(self):
        super().setUp()
        self.crudl = VideoCRUDL
        self.user = User.objects.create_superuser("admin", "a@b.com", "admin")

    def getCreatePostData(self):
        return dict(name="Video One", description="My description", summary="My Summary", vimeo_id="1234", order=0)

    def getUpdatePostData(self):
        return dict(name="Video Updated", description="My description", summary="My Summary", vimeo_id="1234", order=0)
