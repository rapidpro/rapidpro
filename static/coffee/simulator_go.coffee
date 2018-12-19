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
    window.addMessage("Entering the flow \"" + scope.flow.metadata.name + "\"", "log")

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
      if event.type == "broadcast_created"
        window.addMessage("Broadcast sent with text \"" + event.translations[event.base_language].text + "\"", "MT")
      else if event.type == "contact_field_changed"
        window.addMessage("Updated " + event.field.name + " to \"" + event.value.text + "\"", "log")
      else if event.type == "contact_groups_changed"
        if event.groups_added
          for group in event.groups_added
            window.addMessage("Added to group \"" + group.name + "\"", "log")
        if event.groups_removed
          for group in event.groups_removed
            window.addMessage("Removed from group \"" + group.name + "\"", "log")
      else if event.type == "contact_language_changed"
        window.addMessage("Updated language to \"" + event.language + "\"", "log")
      else if event.type == "contact_name_changed"
        window.addMessage("Updated name to \"" + event.name + "\"", "log")
      else if event.type == "contact_timezone_changed"
        window.addMessage("Updated timezone to \"" + event.timezone + "\"", "log")
      else if event.type == "contact_urns_changed"
        msg = "Updated contact URNs to:\n"
        for urn in event.urns
          msg += urn + "\n"
        window.addMessage(msg, "log")
      else if event.type == "email_created"
        msg = "Email sent to "
        delim = ""
        for address in event.addresses
          msg += delim + address
          delim = ", "
        msg += "with subject \"" + event.subject + "\""
        window.addMessage(msg, "log")
      else if event.type == "flow_triggered"
        window.addMessage("Entering the flow \"" + event.flow.name + "\"", "log")
      else if event.type == "input_labels_added"
        msg = "Message labeled with "
        delim = ""
        for label in event.labels
          msg += delim + "\"" + label.name + "\""
          delim = ", "
        window.addMessage(msg, "log")
      else if event.type == "msg_created"
        window.addMessage(event.msg.text, "MT")
        if event.msg.quick_replies?
          quick_replies = "<div class=\"quick-replies\">"
          for reply in event.msg.quick_replies
            quick_replies += "<button class=\"btn quick-reply\" data-payload=\"" + reply + "\"> " + reply + "</button>"
          quick_replies += "</div>"
          $(".simulator-body").append(quick_replies)
      else if event.type == "run_result_changed"
        slugged = event.name.toLowerCase().replace(/([^a-z0-9]+)/g, '_')
        window.addMessage("Saving @flow." + slugged + " as \"" + event.value + "\"", "log")
      else if event.type == "session_triggered"
        window.addMessage("Started other contacts in " + event.flow.name, "log")
      else if event.type == "webhook_called"
        webhookEvent = event
        window.addMessage("Called " + event.url + " which returned " + event.status, "log", null, null, () ->
          modal = showModal("Webhook Result", "<pre>" + webhookEvent.response + "</pre>")
          modal.setListeners({}, true)
          modal.hideSecondaryButton()
        )
      else if event.type == "error"
        window.addMessage(event.text, 'error')
        if (event.fatal)
          $('#simulator').addClass('disabled')

  $(".simulator-body").scrollTop($(".simulator-body")[0].scrollHeight)
  $("#simulator textarea").val("")

  $(".btn.quick-reply").on "click", (event) ->
    payload = event.target.innerText
    window.sendSimulationMessage(payload)

  if data.session
    if data.session.status == 'completed'
      # the initial flow doesn't get a flow exit event
      scope = $("#ctlr").data('$scope')
      window.addMessage("Exited the flow \"" + scope.flow.metadata.name + "\"", "log")
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


