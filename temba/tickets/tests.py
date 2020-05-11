from temba.tests import TembaTest

from .models import Ticket, Ticketer
from .types.mailgun import MailgunType


class TicketerTest(TembaTest):
    def test_release(self):
        ticketer = Ticketer.create(self.org, self.user, MailgunType.slug, "Email (bob@acme.com)", {})

        contact = self.create_contact("Bob", twitter="bobby")

        ticket = Ticket.objects.create(
            org=self.org,
            ticketer=ticketer,
            contact=contact,
            subject="Need help",
            body="Where are my cookies?",
            status="O",
        )

        # release it
        ticketer.release()
        ticketer.refresh_from_db()
        self.assertFalse(ticketer.is_active)

        # ticket should be closed too
        ticket.refresh_from_db()
        self.assertEqual("C", ticket.status)

        # reactivate
        ticketer.is_active = True
        ticketer.save()

        # add a dependency and try again
        flow = self.create_flow()
        flow.ticketer_dependencies.add(ticketer)

        with self.assertRaises(ValueError):
            ticketer.release()

        ticketer.refresh_from_db()
        self.assertTrue(ticketer.is_active)
