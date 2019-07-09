window.simulation = false
window.moving_sim = false
window.level_classes = {"I": "iinfo", "W": "iwarn", "E": "ierror"}
window.legacy = !window.useMailroom

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
  return valid

window.resetForm = ->
    # reset our form input
    $("#simulator textarea").val("")

    # hide loading first
    $(".simulator-loading").css "display", "none"

    $("#simulator textarea").removeClass "error"

processForm = (postData) ->
    # if we are currently saving to don't post message yet
    scope = $('html').scope('scope')
    if scope and scope.saving
      setTimeout ->
        processForm(postData)
      , 500
      return

    if window.legacy
      window.sendUpdateLegacy(postData)
    else
      return window.sendSimUpdate(postData)

window.sendSimMessage = (new_message) ->
  sendMessage(new_message)


sendMessage = (newMessage) ->
  if checkForm(newMessage)
    processForm({new_message: newMessage})
    return true


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

window.updateActivity = (data) ->

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

showSimulator = (reset=false) ->

  messageCount = $(".simulator-body").data('message-count')

  if reset or not messageCount or messageCount == 0
    resetSimulator()
  else
    refreshSimulator()

  window.moving_sim = true
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
      window.moving_sim = false
  window.simulation = true

window.refreshSimulator = ->
  # if we are currently saving to don't post message yet
  scope = $('html').scope('scope')
  if scope and scope.saving
    setTimeout(refreshSimulator, 500)
    return

  if window.legacy
    window.simStartLegacy()
  else
    window.simStart()

resetSimulator = ->
  $('#simulator').removeClass('disabled')
  $(".simulator-body").html("")
  $(".simulator-body").append("<div class='ilog from'>One moment..</div>")
  $(".simulator-loading").css("display", "none")

  # reset our form input
  $('.simulator-footer .media-button').hide()
  $('.simulator-footer .imessage').hide()

  # if we are currently saving to don't post message yet
  scope = $('html').scope('scope')
  if scope and scope.saving
    setTimeout(resetSimulator, 500)
    return

  if window.legacy
    window.simStartLegacy()
  else
    window.simStart()

window.hangup = ->
  $(".simulator-body").html ""
  $.post(getSimulateURL(), JSON.stringify({ hangup:true })).done (data) ->

window.appendMessage = (newMessage, attachments=null, ussd=false) ->
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
  msg = processForm({new_gps: true})
  if msg
    window.addSimMessage("MO", msg.text, msg.attachments)

$('#simulator .photo-button').on 'click', ->
  msg = processForm({new_photo: true})
  if msg
    window.addSimMessage("MO", msg.text, msg.attachments)

$('#simulator .video-button').on 'click', ->
  msg = processForm({new_video: true})
  if msg
    window.addSimMessage("MO", msg.text, msg.attachments)

$('#simulator .audio-button').on 'click', ->
  msg = processForm({new_audio: true})
  if msg
    window.addSimMessage("MO", msg.text, msg.attachments)


# send new message to simulate
$("#simulator .send-message").on "click", ->
  newMessage = $("#simulator textarea").val()
  $(this).addClass("to-ignore")
  if sendMessage(newMessage)
    # add the progress gif
    if window.ussd and newMessage.length <= 182
      window.appendMessage(newMessage, null, true)
    else if newMessage.length <= 160 and newMessage.length > 0
      window.appendMessage(newMessage)

# send new message on key press (enter)
$("#simulator textarea").keypress (event) ->
  if event.which is 13
    event.preventDefault()
    newMessage = $("#simulator textarea").val()
    if sendMessage(newMessage)
      # add the progress gif
      if newMessage
        if window.ussd and newMessage.length <= 182
          window.appendMessage(newMessage, null, true)
        else if newMessage.length <= 160
          window.appendMessage(newMessage)

$("#show-simulator").hover ->
  if not window.moving_sim
    $(this).stop().animate {width: '110px'}, 200, "easeOutBack", ->
      $(this).find('.message').stop().fadeIn('fast')
, ->
  if not window.moving_sim
    $(this).find('.message').hide()
    $(this).stop().animate { width: '40px'}, 200, "easeOutBack", ->

verifyNumberSimulator = ->
  if window.ussd and not window.has_ussd_channel
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
  resetSimulator()

