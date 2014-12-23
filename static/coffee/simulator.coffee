window.simulation = false
moving_sim = false

window.updateSimulator = (data) ->

  $(".simulator-body").html ""
  i = 0

  $('.simulator-body').data('message-count', data.messages.length)
  while i < data.messages.length
    msg = data.messages[i]
    direction = (if (msg.direction is "O") then "from" else "to")
    model = (if (msg.model is "msg") then "imsg" else "ilog")
    $(".simulator-body").append "<div class=\"" + model + " " + direction + "\">" + msg.text + "</div>"
    i++
  $(".simulator-body").scrollTop $(".simulator-body")[0].scrollHeight
  $("#simulator textarea").val ""

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
    footer = currentResized + 20
    resized = currentResized
    $(".simulator-footer").css "height", footer
    $(".simulator-body").css "height", initSimulatorBody - footer + 30
    $(".simulator-body").scrollTop $(".simulator-body")[0].scrollHeight


# check form errors
checkForm = (newMessage) ->
  status = true
  if newMessage is ""
    $("#simulator textarea").addClass "error"
    status = false
  else if newMessage.length > 160
    $("#simulator textarea").val ""
    $("#simulator textarea").addClass "error"
    status = false
  toExpand.css "height", initTextareaHeight
  $(".simulator-footer").css "height", initTextareaHeight + 10
  $(".simulator-body").css "height", "360px"
  status

# process form
processForm = (newMessage) ->
  if checkForm(newMessage)
    $.post(getSimulateURL(), JSON.stringify({ new_message: newMessage })).done (data) ->

      window.updateSimulator(data)

      # hide loading first
      $(".simulator-loading").css "display", "none"
      $(".simulator-body").css "height", "360px"

    $("#simulator textarea").removeClass "error"


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
  # console.log("Messages: " + messageCount)

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
  $.post(getSimulateURL(), JSON.stringify({ has_refresh:false })).done (data) ->
    window.updateSimulator(data)
    if window.ivr and window.simulation
      setTimeout(window.refreshSimulator, 2000)

window.resetSimulator = ->


  $(".simulator-body").html ""
  $(".simulator-body").append "<div class='ilog from'>One moment..</div>"
  $.post(getSimulateURL(), JSON.stringify({ has_refresh:true })).done (data) ->
    window.updateSimulator(data)
    if window.ivr and window.simulation
      setTimeout(window.refreshSimulator, 2000)

window.hangup = ->
  $(".simulator-body").html ""
  $.post(getSimulateURL(), JSON.stringify({ hangup:true })).done (data) ->


#-------------------------------------
# Event bindings
#-------------------------------------

# send new message to simulate
$("#simulator .send-message").on "click", ->
  newMessage = $("#simulator textarea").val()
  $(this).addClass "to-ignore"
  processForm newMessage

  # add the progress gif
  if newMessage and newMessage.length <= 160
    $(".simulator-body").append "<div class=\"imsg to post-message\">" + newMessage + "</div>"
    $("#simulator textarea").val ""
    $(".simulator-loading").css "display", "block"
    # $(".simulator-body").css "height", $(".simulator-body").height() - 25
    $(".simulator-body").scrollTop $(".simulator-body")[0].scrollHeight

# send new message on key press (enter)
$("#simulator textarea").keypress (event) ->
  if event.which is 13
    event.preventDefault()
    newMessage = $("#simulator textarea").val()
    processForm newMessage

    # add the progress gif
    if newMessage and newMessage.length <= 160
      $(".simulator-body").append "<div class=\"imsg to post-message\">" + newMessage + "</div>"
      $("#simulator textarea").val ""
      $(".simulator-loading").css "display", "block"
      # $(".simulator-body").css "height", $(".simulator-body").height() - 25
      $(".simulator-body").scrollTop $(".simulator-body")[0].scrollHeight

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

