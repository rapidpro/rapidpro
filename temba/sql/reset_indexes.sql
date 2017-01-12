CREATE INDEX channels_channellog_channel_created_on ON channels_channellog(channel_id, created_on desc);

CREATE INDEX channels_channelevent_api_view ON channels_channelevent(org_id, created_on DESC, id DESC)
  WHERE is_active = TRUE;

CREATE INDEX channels_channelevent_calls_view ON channels_channelevent(org_id, "time" DESC)
  WHERE is_active = TRUE AND event_type IN ('mt_call', 'mt_miss', 'mo_call', 'mo_miss');

CREATE INDEX org_test_contacts ON contacts_contact (org_id)
  WHERE is_test = TRUE;

CREATE INDEX contacts_contact_org_modified_id_where_nontest_active ON contacts_contact (org_id, modified_on DESC, id DESC)
  WHERE is_test = false AND is_active = true;

CREATE INDEX contacts_contact_org_modified_id_where_nontest_inactive ON contacts_contact (org_id, modified_on DESC, id DESC)
  WHERE is_test = false AND is_active = false;

CREATE INDEX flows_flowrun_expires_on ON flows_flowrun(expires_on)
  WHERE is_active = TRUE;

CREATE INDEX "flows_flowrun_null_expired_on" ON flows_flowrun (exited_on)
  WHERE exited_on IS NULL;

CREATE INDEX flows_flowrun_org_modified_id ON flows_flowrun (org_id, modified_on DESC, id DESC);

CREATE INDEX flows_flowrun_org_modified_id_where_responded ON flows_flowrun (org_id, modified_on DESC, id DESC)
  WHERE responded = TRUE;

CREATE INDEX flows_flowrun_flow_modified_id ON flows_flowrun (flow_id, modified_on DESC, id DESC);

CREATE INDEX flows_flowrun_flow_modified_id_where_responded ON flows_flowrun (flow_id, modified_on DESC, id DESC)
  WHERE responded = TRUE;

CREATE INDEX flows_flowrun_parent_created_on_not_null ON flows_flowrun (parent_id, created_on desc)
  WHERE parent_id IS NOT NULL;

CREATE INDEX flows_flowrun_timeout_active ON flows_flowrun (timeout_on)
  WHERE is_active = TRUE AND timeout_on IS NOT NULL;

CREATE INDEX msgs_msg_responded_to_not_null ON msgs_msg (response_to_id)
  WHERE response_to_id IS NOT NULL;

CREATE INDEX msgs_msg_visibility_type_created_id_where_inbound ON msgs_msg(org_id, visibility, msg_type, created_on DESC, id DESC)
  WHERE direction = 'I';

CREATE INDEX msgs_msg_org_modified_id_where_inbound ON msgs_msg (org_id, modified_on DESC, id DESC)
  WHERE direction = 'I';

CREATE INDEX msgs_msg_org_created_id_where_outbound_visible_outbox ON msgs_msg(org_id, created_on DESC, id DESC)
  WHERE direction = 'O' AND visibility = 'V' AND status IN ('P', 'Q');

CREATE INDEX msgs_msg_org_created_id_where_outbound_visible_sent ON msgs_msg(org_id, created_on DESC, id DESC)
  WHERE direction = 'O' AND visibility = 'V' AND status IN ('W', 'S', 'D');

CREATE INDEX msgs_msg_org_created_id_where_outbound_visible_failed ON msgs_msg(org_id, created_on DESC, id DESC)
  WHERE direction = 'O' AND visibility = 'V' AND status = 'F';

CREATE INDEX msgs_broadcasts_org_created_id_where_active ON msgs_broadcast(org_id, created_on DESC, id DESC)
  WHERE is_active = true;

CREATE INDEX msgs_msg_external_id_where_nonnull ON msgs_msg(external_id)
  WHERE external_id IS NOT NULL;

CREATE INDEX values_value_contact_field_location_not_null ON values_value(contact_field_id, location_value_id)
  WHERE contact_field_id IS NOT NULL AND location_value_id IS NOT NULL;
