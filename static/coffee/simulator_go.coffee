
getStartRequest = ->
  scope = $("#ctlr").data('$scope')

  if window.ivr
    connection = {
      channel: {
        uuid: "440099cf-200c-4d45-a8e7-4a564f4a0e8b",
        name: "Test Channel"
      },
      urn: "tel:+12065551212"
    }
  else
    connection = null

  return {
    trigger: {
      type: "manual",
      contact: {
        uuid: uuid(),
        name: window.contactName,
        urns: ["tel:+12065551212"],
        created_on: new Date(),
      }
      flow: {uuid: scope.flow.metadata.uuid, name: scope.flow.metadata.name},
      connection: connection,
      triggered_on: new Date()
    }
  }


window.simStart = ->
  window.session = null
  window.resetForm()
  request = getStartRequest()
  $.post(getSimulateURL(), JSON.stringify(request)).done (response) ->
    window.session = response.session

    # first clear our body
    $(".simulator-body").html ""

    # the initial flow doesn't get a flow start event
    scope = $("#ctlr").data('$scope')

    window.updateSimResults(response.session, response.events)

window.sendSimUpdate = (postData) ->
  msg = {
    text: postData.new_message or "",
    attachments: [],
    uuid: uuid(),
    urn: "tel:+12065551212",
    created_on: new Date(),
  }
  if postData.new_photo
    msg.attachments.push("image/jpg:" + window.staticURL + "images/simulator/capture.jpg")
  else if postData.new_audio
    msg.attachments.push("audio/m4a:" + window.staticURL + "images/simulator/capture.m4a")
  else if postData.new_video
    msg.attachments.push("video/mp4:" + window.staticURL + "images/simulator/capture.mp4")
  else if postData.new_gps
    msg.attachments.push("geo:47.6089533,-122.34177")

  request = {
    'session': window.session,
    'resume': {
      type: "msg",
      msg: msg,
      resumed_on: new Date(),
      contact: window.session.contact
    }
  }

  $.post(getSimulateURL(), JSON.stringify(request)).done (response) ->
    window.session = response.session
    window.updateSimResults(response.session, response.events)
    window.resetForm()

  return msg

window.showModal = (title, body) ->
  modal = new ConfirmationModal(title, body);
  modal.show();
  return modal


window.trigger = null

window.updateSimResults = (session, events) ->
  if window.trigger == null || window.trigger.triggered_on != session.trigger.triggered_on
    if window.trigger != null && !window.trigger.exited
      window.addSimMessage("log", "Exited the flow \"" + window.trigger.flow.name + "\"")

    window.trigger = session.trigger
    window.trigger.exited = false
    window.addSimMessage("log", "Entered the flow \"" + session.trigger.flow.name + "\"")

  if events
    for event in events
      window.renderSimEvent(event)

  $("#simulator textarea").val("")

  $(".btn.quick-reply").unbind('click').on("click", (event) ->
    payload = event.target.innerText
    window.appendMessage(payload)
    window.sendSimMessage(payload)
  )

  if session
    if session.status == 'completed'
      if !window.trigger.exited
        window.addSimMessage("log", "Exited the flow \"" + window.trigger.flow.name + "\"")
        window.trigger.exited = true

      window.handleSimWait(null)
    else if session.status == 'waiting'
      window.handleSimWait(session.wait)

    # we need to construct the old style activity format
    visited = {}

    lastExit = null
    for run in session.runs
      for segment in run.path
        if lastExit
          key = lastExit + ':' + segment.node_uuid
          if key not of visited
            visited[key] = 0
          visited[key] = visited[key] + 1

        lastExit = segment.exit_uuid
        activity = {}
        activity[segment.node_uuid] = 1

    updateActivity({'activity': activity, 'visited': visited})


window.renderSimEvent = (event) ->
  switch event.type
    when "broadcast_created"
      window.addSimMessage("MT", "Broadcast sent with text \"" + event.translations[event.base_language].text + "\"")

    when "contact_field_changed"
      text = ''
      if event.value
        text = event.value.text
      window.addSimMessage("log", "Updated " + event.field.name + " to \"" + text + "\"")

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

    when "ivr_created", "msg_created"
      $(".btn.quick-reply").hide()
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
      window.addSimMessage("log", "Called " + event.url + " which returned " + event.status + " in " + webhookEvent.elapsed_ms + "ms", null, () ->
        body = "<pre>" + webhookEvent.request + "</pre>"
        body += "<pre>" + webhookEvent.response + "</pre>"

        modal = showModal("Webhook Result", body)
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
      media_type = attachment.split(':')[0]
      url = attachment.split(":").slice(1).join(":")

      classes.push("media-msg")

      if media_type == 'geo'
        media_viewer = '<div class="media-file">' + url + '</div>'
      else
        if media_type.indexOf('/') > 0
          media_type = media_type.split('/')[0]

        if not url.startsWith("http") and not url.startsWith("/sitestatic/")
          url = window.mediaURL + url

        if media_type == 'image'
          media_viewer = '<div class="media-file"><img src="' + url + '"></div>'
        else if media_type == 'video'
          media_viewer = '<div class="media-file"><video controls src="' + url + '" style="width: 150px"></div>'
        else if media_type == 'audio'
          media_viewer = '<div class="media-file"><audio controls src="' + url + '" style="width: 150px"></div>'

  ele = '<div class="' + classes.join(" ") + '">'
  ele += text
  if media_viewer
      ele += media_viewer
  ele += '</div>'

  ele = $(ele)
  if onClick
    ele.bind("click", onClick)

  ele = $(".simulator-body").append(ele)
  $(".simulator-body").scrollTop($(".simulator-body")[0].scrollHeight)


window.setSimQuickReplies = (replies) ->
  quick_replies = "<div class=\"quick-replies\">"
  for reply in replies
    quick_replies += "<button class=\"btn quick-reply\" data-payload=\"" + reply + "\"> " + reply + "</button>"
  quick_replies += "</div>"
  $(".simulator-body").append(quick_replies)


#============================================================================
# Handles a wait in the current simulator session
#============================================================================
window.handleSimWait = (wait) ->
  if wait == null
    $('.simulator-footer .media-button').hide()
    $('.simulator-footer .imessage').show()
    return

  window.currentSimWait = wait
  window.showSimKeypad(false)

  $('.simulator-footer .media-button').hide()

  if wait.hint?
    switch wait.hint.type
      when "image"
        $('.simulator-footer .imessage').hide()
        $('.simulator-footer .photo-button').show()
      when "video"
        $('.simulator-footer .imessage').hide()
        $('.simulator-footer .video-button').show()
      when "audio"
        $('.simulator-footer .imessage').hide()
        $('.simulator-footer .audio-button').show()
      when "location"
        $('.simulator-footer .imessage').hide()
        $('.simulator-footer .gps-button').show()
      when "digits"
        $('.simulator-footer .imessage').hide()
        window.showSimKeypad(true)

  else
    $('.simulator-footer .imessage').show()


#============================================================================
# Displays the simulator key pad
#============================================================================
window.showSimKeypad = (show) ->
  normalBodyHeight = 360
  normalFooterHeight = 35
  keypadHeight = 145

  if show
    $('.simulator-body').height(normalBodyHeight - keypadHeight)
    $('.simulator-footer').height(normalFooterHeight + keypadHeight)
    $('.simulator-footer .keypad').show()
  else
    $('.simulator-body').height(normalBodyHeight)
    $('.simulator-footer').height(normalFooterHeight)
    $('.simulator-footer .keypad').hide()


#============================================================================
# Handles button press on simulator keypad
#============================================================================
$('#simulator .keypad .btn').on('click', ->
  keypadDisplay = $('.simulator-footer .keypad .display')
  keypadDisplay.text(keypadDisplay.text() + $(this).text())

  if window.currentSimWait?
    wait = window.currentSimWait
    submit = false

    if wait.hint.count? and keypadDisplay.text().length >= wait.hint.count
      submit = true
    else if keypadDisplay.text().endsWith("#")
      submit = true

    if submit
      window.showSimKeypad(false)
      window.appendMessage(keypadDisplay.text())
      window.sendSimMessage(keypadDisplay.text())
      keypadDisplay.text("")
)

