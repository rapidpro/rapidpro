# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models
from smartmin.models import SmartModel
from temba.orgs.models import Org
from temba.flows.models import FlowRevision
# Create your models here.

class Notification(SmartModel):
    CAMPAIGN_TYPE = "C"
    FLOW_TYPE = "F"
    TRIGGER_TYPE = "T"
    EVENT_TYPE = "E"
    TYPE_CHOICES = ((CAMPAIGN_TYPE, "Campaign_type"),
                    (FLOW_TYPE, "Flow_type"),
                    (EVENT_TYPE, "EventCampaign_type"),
                    (TRIGGER_TYPE, "Trigger_type"))
    """
    An Notification to send flow from staging organization to production.
    """

    org_dest = models.ForeignKey(Org, verbose_name="Org_destine", related_name="org_dest",
                                    help_text="The destination organization")
    org_orig = models.ForeignKey(Org, verbose_name="Org_origin", null=True, related_name="org_origin",
                                    blank=True, on_delete=models.SET_NULL,
                                    help_text="The origin organization")
    note_orig = models.CharField(verbose_name="Note origin", max_length=250,
                                    null=True, blank=True,
                                    help_text="Notes of notification to admin")
    note_dest = models.CharField(verbose_name="Note dest", max_length=250,
                                    null=True, blank=True,
                                    help_text="Notes of notification to user")
    item_id   = models.IntegerField(default=0,
                                    help_text="Id of item")
    item_type = models.CharField(max_length=1, choices=TYPE_CHOICES, null=True,
                                 help_text="Type of item")
    item_name = models.CharField(max_length=200, verbose_name ="Name of item", null = True, blank=True,
                                 help_text ="Name of item")
    history = models.CharField(max_length=90000, verbose_name = "Version of item", null = True, blank=True,
                                 help_text ="Version of item")
    accepted = models.BooleanField(default=False,
                                  help_text="Whether this notification was accepted by administrator")
    auto_migrated = models.BooleanField(default=False,
                                  help_text="Whether the notification is auto_migrated")
    reviewed = models.BooleanField(default=False,
                                  help_text="Whether the item was reviewed")
    to_archive = models.BooleanField(default=False,
                                  help_text="Whether the item has to be archive")
    archived = models.BooleanField(default=False,
                                  help_text="Notification already archived")
    history_dump = models.CharField(max_length=2000, verbose_name = "History of changes", null = True, blank=True,
                                 help_text ="History of changes")
    migrated = models.BooleanField(default=False,
                              help_text="Whether this notification was migrated")

    @classmethod
    def create_from_staging(cls, user, org_orig, org_dest, item_type,
                            item_id, item_name, history, auto_migrated,
                            note=None, history_dump = None):
        """
        Class method to create a notification from user to admin

        :param cls: Notification model class
        :param user: User who created this notification
        :param org_orig: Orgatization who create this notification
        :param org_dest: Organization to migrate this notification
        :param item_type: Type of item to migrate (Campaign, Flow, Trigger)
        :param item_id: Id of item to refer
        :param item_name: Name of item
        :param history: FlowRevision with the last changes
        :auto_migrated: Boolean if this notification was auto_migrated
        :return Notification created instance
        """
        return cls.objects.create(org_orig=org_orig,
                                  org_dest=org_dest,
                                  item_type = item_type,
                                  item_id = item_id,
                                  item_name = item_name,
                                  history=history,
                                  created_by=user,
                                  modified_by=user,
                                  note_orig = note,
                                  auto_migrated = auto_migrated,
                                  history_dump = history_dump)


    def set_accepted(self, status):
        """
        Method to mark as accepted this notification

        :param self: Instance of class
        :param status: Boolean value of accepted
        """
        self.accepted = status
        self.save()

    def set_to_archive(self, status):
        """
        Method to mark as accepted this notification

        :param self: Instance of class
        :param status: Boolean value of accepted
        """
        self.to_archive = status
        self.save()

    def mark_migrated(self):
        """
        Method to mark as desactive this notification

        :param self: Instance of class
        """
        self.migrated = True
        self.save()

    def set_reviewed(self, status):
        """
        Method to set reviewed this notification

        :param self: Instance of class
        :param status: Boolean value of reviewed
        """
        self.reviewed = status
        self.save()

    def mark_archived(self):
        """
        Method to mark as archived this notification

        :param self: Instance of class
        """
        self.archived = True
        self.save()
