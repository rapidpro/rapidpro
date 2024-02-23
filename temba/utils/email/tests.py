from temba.tests import TembaTest

from .conf import make_smtp_url, parse_smtp_url
from .send import EmailSender
from .validate import is_valid_address


class EmailTest(TembaTest):
    def test_sender(self):
        branding = {"name": "Test", "emails": {"spam": "no-reply@acme.com"}}
        sender = EmailSender.from_email_type(branding, "spam")
        self.assertEqual(branding, sender.branding)
        self.assertIsNone(sender.connection)  # use default
        self.assertEqual("no-reply@acme.com", sender.from_email)

        # test email type not defined in branding
        sender = EmailSender.from_email_type(branding, "marketing")
        self.assertEqual(branding, sender.branding)
        self.assertIsNone(sender.connection)
        self.assertEqual("server@temba.io", sender.from_email)  # from settings

        # test full SMTP url in branding
        branding = {"name": "Test", "emails": {"spam": "smtp://foo:sesame@acme.com/?tls=true&from=no-reply%40acme.com"}}
        sender = EmailSender.from_email_type(branding, "spam")
        self.assertEqual(branding, sender.branding)
        self.assertIsNotNone(sender.connection)
        self.assertEqual("no-reply@acme.com", sender.from_email)

    def test_is_valid_address(self):
        valid_emails = [
            # Cases from https://en.wikipedia.org/wiki/Email_address
            "prettyandsimple@example.com",
            "very.common@example.com",
            "disposable.style.email.with+symbol@example.com",
            "other.email-with-dash@example.com",
            "x@example.com",
            '"much.more unusual"@example.com',
            '"very.unusual.@.unusual.com"@example.com'
            '"very.(),:;<>[]".VERY."very@\\ "very".unusual"@strange.example.com',
            "example-indeed@strange-example.com",
            "#!$%&'*+-/=?^_`{}|~@example.org",
            '"()<>[]:,;@\\"!#$%&\'-/=?^_`{}| ~.a"@example.org',
            '" "@example.org',
            "example@localhost",
            "example@s.solutions",
            # Cases from Django tests
            "email@here.com",
            "weirder-email@here.and.there.com",
            "email@[127.0.0.1]",
            "email@[2001:dB8::1]",
            "email@[2001:dB8:0:0:0:0:0:1]",
            "email@[::fffF:127.0.0.1]",
            "example@valid-----hyphens.com",
            "example@valid-with-hyphens.com",
            "test@domain.with.idn.tld.उदाहरण.परीक्षा",
            "email@localhost",
            '"test@test"@example.com',
            "example@atm.%s" % ("a" * 63),
            "example@%s.atm" % ("a" * 63),
            "example@%s.%s.atm" % ("a" * 63, "b" * 10),
            '"\\\011"@here.com',
            "a@%s.us" % ("a" * 63),
        ]

        invalid_emails = [
            # Cases from https://en.wikipedia.org/wiki/Email_address
            None,
            "",
            "abc",
            "a@b",
            " @ .c",
            "a @b.c",
            "{@flow.email}",
            "Abc.example.com",
            "A@b@c@example.com",
            r'a"b(c)d,e:f;g<h>i[j\k]l@example.com'
            'just"not"right@example.com'
            'this is"not\allowed@example.com'
            r'this\ still"not\\allowed@example.com'
            "1234567890123456789012345678901234567890123456789012345678901234+x@example.com"
            "john..doe@example.com"
            "john.doe@example..com"
            # Cases from Django tests
            "example@atm.%s" % ("a" * 64),
            "example@%s.atm.%s" % ("b" * 64, "a" * 63),
            None,
            "",
            "abc",
            "abc@",
            "abc@bar",
            "a @x.cz",
            "abc@.com",
            "something@@somewhere.com",
            "email@127.0.0.1",
            "email@[127.0.0.256]",
            "email@[2001:db8::12345]",
            "email@[2001:db8:0:0:0:0:1]",
            "email@[::ffff:127.0.0.256]",
            "example@invalid-.com",
            "example@-invalid.com",
            "example@invalid.com-",
            "example@inv-.alid-.com",
            "example@inv-.-alid.com",
            'test@example.com\n\n<script src="x.js">',
            # Quoted-string format (CR not allowed)
            '"\\\012"@here.com',
            "trailingdot@shouldfail.com.",
            # Max length of domain name labels is 63 characters per RFC 1034.
            "a@%s.us" % ("a" * 64),
            # Trailing newlines in username or domain not allowed
            "a@b.com\n",
            "a\n@b.com",
            '"test@test"\n@example.com',
            "a@[127.0.0.1]\n",
        ]

        for email in valid_emails:
            self.assertTrue(is_valid_address(email), "FAILED: %s should be a valid email" % email)

        for email in invalid_emails:
            self.assertFalse(is_valid_address(email), "FAILED: %s should be an invalid email" % email)

    def test_make_smtp_url(self):
        self.assertEqual(
            "smtp://foo:sesame@gmail.com:25/",
            make_smtp_url("gmail.com", 25, "foo", "sesame", from_email=None, tls=False),
        )
        self.assertEqual(
            "smtp://foo%25:ses%2Fame@gmail.com:457/?from=foo%40gmail.com&tls=true",
            make_smtp_url("gmail.com", 457, "foo%", "ses/ame", "foo@gmail.com", tls=True),
        )

    def test_parse_smtp_url(self):
        self.assertEqual((None, 25, None, None, None, False), parse_smtp_url(None))
        self.assertEqual((None, 25, None, None, None, False), parse_smtp_url(""))
        self.assertEqual(
            ("gmail.com", 25, "foo", "sesame", None, False),
            parse_smtp_url("smtp://foo:sesame@gmail.com/?tls=false"),
        )
        self.assertEqual(
            ("gmail.com", 25, "foo", "sesame", None, True),
            parse_smtp_url("smtp://foo:sesame@gmail.com:25/?tls=true"),
        )
        self.assertEqual(
            ("gmail.com", 457, "foo%", "ses/ame", "foo@gmail.com", True),
            parse_smtp_url("smtp://foo%25:ses%2Fame@gmail.com:457/?tls=true&from=foo%40gmail.com"),
        )
        self.assertEqual((None, 25, None, None, "foo@gmail.com", False), parse_smtp_url("smtp://?from=foo%40gmail.com"))
