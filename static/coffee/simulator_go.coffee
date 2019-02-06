getRequest = ->
  return {
  }

getStartRequest = ->
  scope = $("#ctlr").data('$scope')
  request = getRequest()

  request['trigger'] = {
    type: "manual",
    contact: {
      uuid: uuid(),
      name: contactName,
      urns: ["tel:+12065551212"],
      created_on: new Date(),
    }
    flow: {uuid: scope.flow.metadata.uuid, name: scope.flow.metadata.name}
    triggered_on: new Date()
  }
  return request

window.simStart = ->
  window.session = null
  window.resetForm()
  request = getStartRequest()
  $.post(getSimulateURL(), JSON.stringify(request)).done (results) ->
    window.session = results.session

    # first clear our body
    $(".simulator-body").html ""

    # the initial flow doesn't get a flow start event
    scope = $("#ctlr").data('$scope')
    window.addSimMessage("log", "Entering the flow \"" + scope.flow.metadata.name + "\"")

    window.updateResults(results)

window.sendUpdate = (postData) ->
  request = getRequest()
  request['session'] = window.session
  request['resume'] = {
    type: "msg",
    msg: {
      text: postData.new_message,
      uuid: uuid(),
      urn: "tel:+12065551212",
      created_on: new Date(),
    },
    resumed_on: new Date(),
    contact: window.session.contact
  }

  $.post(getSimulateURL(), JSON.stringify(request)).done (results) ->
    window.session = results.session
    window.updateResults(results)
    window.resetForm()

window.showModal = (title, body) ->
  modal = new ConfirmationModal(title, body);
  modal.show();
  return modal

window.updateResults = (data) ->

  if data.events
    for event in data.events
      window.renderSimEvent(event)

  $(".simulator-body").scrollTop($(".simulator-body")[0].scrollHeight)
  $("#simulator textarea").val("")

  $(".btn.quick-reply").on "click", (event) ->
    payload = event.target.innerText
    window.sendSimulationMessage(payload)

  if data.session
    if data.session.status == 'completed'
      # the initial flow doesn't get a flow exit event
      scope = $("#ctlr").data('$scope')
      window.addSimMessage("log", "Exited the flow \"" + scope.flow.metadata.name + "\"")
      $('#simulator').addClass('disabled')

    # we need to construct the old style activity format
    visited = {}

    lastExit = null
    for run in data.session.runs
      for segment in run.path
        if lastExit
          key = lastExit + ':' + segment.node_uuid
          if key not of visited
            visited[key] = 0
          visited[key] = visited[key] + 1

        lastExit = segment.exit_uuid
        activity = {}
        activity[segment.node_uuid] = 1

    legacyFormat = {
      'activity': activity,
      'visited': visited
    }

    updateActivity(legacyFormat)


window.renderSimEvent = (event) ->
  switch event.type
    when "broadcast_created"
      window.addSimMessage("MT", "Broadcast sent with text \"" + event.translations[event.base_language].text + "\"")

    when "contact_field_changed"
      window.addSimMessage("log", "Updated " + event.field.name + " to \"" + event.value.text + "\"")

    when "contact_groups_changed"
      if event.groups_added
        for group in event.groups_added
          window.addSimMessage("log", "Added to group \"" + group.name + "\"")
      if event.groups_removed
        for group in event.groups_removed
          window.addSimMessage("log", "Removed from group \"" + group.name + "\"")

    when "contact_language_changed"
      window.addSimMessage("log", "Updated language to \"" + event.language + "\"")

    when "contact_name_changed"
      window.addSimMessage("log", "Updated name to \"" + event.name + "\"")

    when "contact_timezone_changed"
      window.addSimMessage("log", "Updated timezone to \"" + event.timezone + "\"")

    when "contact_urns_changed"
      msg = "Updated contact URNs to:\n"
      for urn in event.urns
        msg += urn + "\n"
      window.addSimMessage("log", msg)

    when "email_created"
      msg = "Email sent to "
      delim = ""
      for address in event.addresses
        msg += delim + address
        delim = ", "
      msg += "with subject \"" + event.subject + "\""
      window.addSimMessage("log", msg)

    when "error"
      window.addSimMessage("error", event.text)
      if (event.fatal)
        $('#simulator').addClass('disabled')

    when "flow_entered", "flow_triggered"
      window.addSimMessage("log", "Entering the flow \"" + event.flow.name + "\"")

    when "input_labels_added"
      msg = "Message labeled with "
      delim = ""
      for label in event.labels
        msg += delim + "\"" + label.name + "\""
        delim = ", "
      window.addSimMessage("log", msg)

    when "msg_created"
      window.addSimMessage("MT", event.msg.text, event.msg.attachments)

      if event.msg.quick_replies?
        window.setSimQuickReplies(event.msg.quick_replies)

    when "run_result_changed"
      slugged = event.name.toLowerCase().replace(/([^a-z0-9]+)/g, '_')
      window.addSimMessage("log", "Saving @flow." + slugged + " as \"" + event.value + "\"")

    when "session_triggered"
      window.addSimMessage("log", "Started other contacts in " + event.flow.name)

    when "webhook_called"
      webhookEvent = event
      window.addSimMessage("log", "Called " + event.url + " which returned " + event.status, null, () ->
        modal = showModal("Webhook Result", "<pre>" + webhookEvent.response + "</pre>")
        modal.setListeners({}, true)
        modal.hideSecondaryButton()
      )


window.addSimMessage = (type, text, attachments=null, onClick=null) ->
  classes = ["imsg"]
  media_viewer = null

  if type == "log" or type == "error"
    classes = ["ilog"]

  if type == "MO"
    classes.push("to")
  else if type == "MT"
    classes.push("from")
  else if type == "error"
    classes.push("ierror")

  if onClick
    classes.push("link")

  if attachments and attachments.length > 0
      attachment = attachments[0]
      [media_type, url] = attachment.split(':', 2)

      classes.push("media-msg")

      if media_type != 'geo'
        media_type = media_type.split('/')[0]

        if not url.startsWith("http")
          url = window.mediaURL + url

        if media_type == 'image'
          media_viewer = "<span class=\"media-file\"><img src=\"" + url + "\"></span>"
        else if media_type == 'video'
          media_viewer = "<span class=\"media-file\"><video controls src=\"" + url + "\"></span>"
        else if media_type == 'audio'
          media_viewer = "<span class=\"media-file\"><audio controls src=\"" + url + "\"></span>"

  ele = '<div class="' + classes.join(" ") + '">' + text + '</div>'
  if media_viewer
      ele += media_viewer

  ele = $(ele)
  if onClick
    ele.bind("click", onClick)

  ele = $(".simulator-body").append(ele)


window.setSimQuickReplies = (replies) ->
  quick_replies = "<div class=\"quick-replies\">"
  for reply in replies
    quick_replies += "<button class=\"btn quick-reply\" data-payload=\"" + reply + "\"> " + reply + "</button>"
  quick_replies += "</div>"
  $(".simulator-body").append(quick_replies)
