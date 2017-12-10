window.simulation = false
moving_sim = false
level_classes = {"I": "iinfo", "W": "iwarn", "E": "ierror"}

window.updateSimulator = (data) ->
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

  $(".btn.quick-reply").on "click", (event) ->
    payload = event.target.innerText
    sendMessage(payload)

  if window.simulation

    # this is for angular to show activity
    scope = $('body').scope()
    if scope
      scope.$apply ->
        scope.visibleActivity =
          active: data.activity
          visited: data.visited

    for node in $('#workspace').children('.node')
      node = $(node).data('object')
      node.setActivity(data)

  activity = $('.activity:visible,.node .active:visible')
  if activity
    if activity.offset()
      top = activity.offset().top
      $('html, body').animate
        scrollTop : top - 200

$ ->
  $(window).scroll (evt) ->
    fitSimToScreen()

# Textarea expansion
# [eric] not entirely sure what sort of magic is happening here
toExpand = $("#simulator textarea")
initTextareaHeight = toExpand.height()
initSimulatorBody = $(".simulator-body").height()
resized = toExpand.height()
toExpand.autosize callback: ->
  currentResized = toExpand.height()
  unless currentResized is resized
    footer = currentResized + 10
    resized = currentResized
    $(".simulator-footer").css "height", footer
    $(".simulator-body").css "height", initSimulatorBody - footer + 30
    $(".simulator-body").scrollTop $(".simulator-body")[0].scrollHeight


# check form errors
checkForm = (newMessage) ->
  valid = true
  if newMessage is ""
    $("#simulator textarea").addClass "error"
    valid = false
  else if newMessage.length > 160
    $("#simulator textarea").val ""
    $("#simulator textarea").addClass "error"
    valid = false
  toExpand.css "height", initTextareaHeight
  $(".simulator-footer").css "height", initTextareaHeight + 10
  $(".simulator-body").css "height", "360px"
  return valid

processForm = (postData) ->
    # if we are currently saving to don't post message yet
    scope = $('html').scope('scope')
    if scope and scope.saving
      setTimeout ->
        processForm(postData)
      , 500
      return

    $.post(getSimulateURL(), JSON.stringify(postData)).done (data) ->

      # reset our form input
      $('.simulator-footer .media-button').hide()
      $('.simulator-footer .imessage').show()
      window.updateSimulator(data)

      # hide loading first
      $(".simulator-loading").css "display", "none"
      $(".simulator-body").css "height", "360px"

    $("#simulator textarea").removeClass "error"

sendMessage = (newMessage) ->
  if checkForm(newMessage)
    processForm({new_message: newMessage})

sendPhoto = ->
  processForm({new_photo: true})

sendVideo = ->
  processForm({new_video: true})

sendAudio = ->
  processForm({new_audio: true})

sendGPS = ->
  processForm({new_gps: true})

fitSimToScreen = ->
  top = $(window).scrollTop()
  sim = $("#simulator")
  workspace = $("#workspace")
  showSim = $("#show-simulator")

  if top > 110 and not sim.hasClass('scrollfix')
    sim.addClass('scrollfix')
    showSim.addClass('scrollfix')
  else if top <= 110 and sim.hasClass('scrollfix')
    sim.removeClass('scrollfix')
    showSim.removeClass('scrollfix')

  simTop = sim.offset().top
  simBottom = sim.height() + simTop
  workspaceBottom = workspace.offset().top + workspace.height()

  if simTop > top + 10 and sim.hasClass('on-footer')
    sim.removeClass('on-footer')
  else
    if simBottom > workspaceBottom - 30 and not sim.hasClass('on-footer')
      sim.addClass('on-footer')

    if simBottom < workspaceBottom and sim.hasClass('on-footer')
      sim.removeClass('on-footer')

hideSimulator = ->
  moving_sim = true
  sim = $("#simulator")
  sim.animate right: - (sim.outerWidth() + 10), 400, "easeOutExpo", ->
    sim.hide()
    showButton = $("#show-simulator")
    showButton.css({ right:  - (showButton.outerWidth() + 10)})
    showButton.show()
    showButton.stop().animate { right:0, width: 40 }, 400, "easeOutExpo"
    moving_sim = false

  window.simulation = false
  $("#toolbar .actions").fadeIn();

  # this is the hook into angular
  # show our normal activity when the sim is hidden
  scope = $('body').scope()
  if scope
    scope.$apply ->
      scope.visibleActivity = scope.activity

  if window.is_voice
    window.hangup()


getSimulateURL = ->
  scope = $('html').scope()
  if scope and scope.language
    return window.simulateURL + '?lang=' + scope.language.iso_code
  return window.simulateURL

showSimulator = (reset=false) ->

  messageCount = $(".simulator-body").data('message-count')

  if reset or not messageCount or messageCount == 0
    resetSimulator()
  else
    refreshSimulator()

  moving_sim = true
  fitSimToScreen()
  $("#toolbar .actions").fadeOut();
  $("#show-simulator").stop().animate { right: '-110px' }, 200, ->
    $(this).hide()
    $(this).find('.message').hide()
    sim = $("#simulator")
    sim.css({ right:  - (sim.outerWidth() + 10)})
    sim.show()
    sim.animate right: 30, 400, "easeOutExpo", ->
      $(".simulator-content textarea").focus()
      moving_sim = false
  window.simulation = true

window.refreshSimulator = ->

  # if we are currently saving to don't post message yet
  scope = $('html').scope('scope')
  if scope and scope.saving
    setTimeout(refreshSimulator, 500)
    return

  $.post(getSimulateURL(), JSON.stringify({ has_refresh:false })).done (data) ->
    window.updateSimulator(data)
    if window.ivr and window.simulation
      setTimeout(window.refreshSimulator, 2000)

window.resetSimulator = ->
  $(".simulator-body").html ""
  $(".simulator-body").append "<div class='ilog from'>One moment..</div>"

  # reset our form input
  $('.simulator-footer .media-button').hide()
  $('.simulator-footer .imessage').hide()

  # if we are currently saving to don't post message yet
  scope = $('html').scope('scope')
  if scope and scope.saving
    setTimeout(resetSimulator, 500)
    return

  $.post(getSimulateURL(), JSON.stringify({ has_refresh:true })).done (data) ->
    window.updateSimulator(data)
    if window.ivr and window.simulation
      setTimeout(window.refreshSimulator, 2000)

window.hangup = ->
  $(".simulator-body").html ""
  $.post(getSimulateURL(), JSON.stringify({ hangup:true })).done (data) ->

appendMessage = (newMessage, ussd=false) ->
  ussd = if ussd then "ussd " else ""
  imsgDiv = '<div class=\"imsg ' + ussd + 'to post-message\"></div>'
  $(imsgDiv).text(newMessage).appendTo(".simulator-body")
  $("#simulator textarea").val ""
  $(".simulator-loading").css "display", "block"
  # $(".simulator-body").css "height", $(".simulator-body").height() - 25
  $(".simulator-body").scrollTop $(".simulator-body")[0].scrollHeight

#-------------------------------------
# Event bindings
#-------------------------------------

$('#simulator .gps-button').on 'click', ->
  sendGPS();

$('#simulator .photo-button').on 'click', ->
  sendPhoto()

$('#simulator .video-button').on 'click', ->
  sendVideo()

$('#simulator .audio-button').on 'click', ->
  sendAudio()

# send new message to simulate
$("#simulator .send-message").on "click", ->
  newMessage = $("#simulator textarea").val()
  $(this).addClass("to-ignore")
  sendMessage(newMessage)

  # add the progress gif
  if window.ussd and newMessage.length <= 182
    appendMessage newMessage, true
  else if newMessage.length <= 160 and newMessage.length > 0
    appendMessage newMessage

# send new message on key press (enter)
$("#simulator textarea").keypress (event) ->
  if event.which is 13
    event.preventDefault()
    newMessage = $("#simulator textarea").val()
    sendMessage(newMessage)

    # add the progress gif
    if newMessage
      if window.ussd and newMessage.length <= 182
        appendMessage newMessage, true
      else if newMessage.length <= 160
        appendMessage newMessage

$("#show-simulator").hover ->
  if not moving_sim
    $(this).stop().animate {width: '110px'}, 200, "easeOutBack", ->
      $(this).find('.message').stop().fadeIn('fast')
, ->
  if not moving_sim
    $(this).find('.message').hide()
    $(this).stop().animate { width: '40px'}, 200, "easeOutBack", ->

verifyNumberSimulator = ->
  if window.ivr
    modal = new Modax(gettext("Start Test Call"), '/usersettings/phone/')
    modal.setIcon("icon-phone")
    modal.setListeners
      onSuccess: ->
        showSimulator(true)
    modal.show()

  else if window.ussd and not window.has_ussd_channel
    modal = new Modal(gettext("Missing USSD Channel"), gettext("There is no channel that supports USSD connected. Please connect a USSD channel first."))
    modal.setIcon("icon-phone")
    modal.setListeners
      onPrimary: ->
        modal.dismiss()
    modal.show()
  else
    showSimulator()

$("#show-simulator").click ->
  verifyNumberSimulator()

# toggle simulator
$("#toggle-simulator").on "click", ->
  if not $("#simulator").is(":visible")
    verifyNumberSimulator()
  else
    hideSimulator()

# close the simulator
$(".simulator-close").on "click", ->
  hideSimulator()

# refresh the simulator
$(".simulator-refresh").on "click", ->
  window.resetSimulator()

