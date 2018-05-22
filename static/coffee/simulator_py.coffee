window.getSimulateURL = ->
  scope = $('html').scope()
  if scope and scope.language
    return window.simulateURL + '?lang=' + scope.language.iso_code
  return window.simulateURL

window.simStartLegacy = ->
  $.post(getSimulateURL(), JSON.stringify({ has_refresh:true, version:"1" })).done (results) ->
    window.updateResultsLegacy(results)
    if window.ivr and window.simulation
      setTimeout(window.refreshSimulator, 2000)

window.sendUpdateLegacy = (postData) ->
  postData['version'] = "1"
  $.post(getSimulateURL(), JSON.stringify(postData)).done (results) ->
    window.resetForm()
    window.updateResultsLegacy(results)

window.updateResultsLegacy = (data) ->
  ussd = if window.ussd then "ussd" else ""

  $(".simulator-body").html ""
  i = 0

  $('.simulator-body').data('message-count', data.messages.length)

  if data.ruleset
    $('.simulator-footer .media-button').hide()

    if data.ruleset.ruleset_type == 'wait_gps'
      $('.simulator-footer .imessage').hide()
      $('.simulator-footer .gps-button').show()
    else if data.ruleset.ruleset_type == 'wait_photo'
      $('.simulator-footer .imessage').hide()
      $('.simulator-footer .photo-button').show()
    else if data.ruleset.ruleset_type == 'wait_video'
      $('.simulator-footer .imessage').hide()
      $('.simulator-footer .video-button').show()
    else if data.ruleset.ruleset_type == 'wait_audio'
      $('.simulator-footer .imessage').hide()
      $('.simulator-footer .audio-button').show()
    else
      $('.simulator-footer .imessage').show()
  else
    $('.simulator-footer .media-button').hide()
    $('.simulator-footer .imessage').show()


  while i < data.messages.length
    msg = data.messages[i]

    model = (if (msg.model is "msg") then "imsg" else "ilog")
    level = (if msg.level? then level_classes[msg.level] else "")
    direction = (if (msg.direction is "O") then "from" else "to")

    media_type = null
    media_viewer_elt = null

    quick_replies = null

    metadata = msg.metadata
    if metadata and metadata.quick_replies?
      quick_replies = "<div id='quick-reply-content'>"
      for reply in metadata.quick_replies
        quick_replies += "<button class=\"btn quick-reply\" data-payload=\"" + reply + "\"> " + reply + "</button>"
      quick_replies += "</div>"

    if msg.attachments and msg.attachments.length > 0
      attachment = msg.attachments[0]
      parts = attachment.split(':')
      media_type = parts[0]
      media_url = parts.slice(1).join(":")

      if media_type == 'geo'
        media_type = 'icon-pin_drop'
      else
        media_type = media_type.split('/')[0]
        if media_type == 'image'
          media_type = 'icon-photo_camera'
          media_viewer_elt = "<span class=\"media-file\"><img src=\"" + media_url + "\"></span>"
        else if media_type == 'video'
          media_type = 'icon-videocam'
          media_viewer_elt = "<span class=\"media-file\"><video controls src=\"" + media_url + "\"></span>"
        else if media_type == 'audio'
          media_type = 'icon-mic'
          media_viewer_elt = "<span class=\"media-file\"><audio controls src=\"" + media_url + "\"></span>"

    ele = "<div class=\"" + model + " " + level + " " + direction + " " + ussd
    if media_type
      ele += " media-msg"
    ele += "\">"
    ele += msg.text
    ele += "</div>"

    if quick_replies
      ele_quick_replies = "<div class='ilog " + level + " " + direction + " " + ussd + "'>"
      ele_quick_replies += quick_replies
      ele_quick_replies += "</div>"
      ele += ele_quick_replies

    if media_type and media_viewer_elt
      ele += media_viewer_elt

    $(".simulator-body").append(ele)
    i++
  $(".simulator-body").scrollTop $(".simulator-body")[0].scrollHeight
  $("#simulator textarea").val ""
  $(".simulator-content textarea").focus()

  $(".btn.quick-reply").on "click", (event) ->
    payload = event.target.innerText
    sendMessage(payload)

  window.updateActivity(data)
