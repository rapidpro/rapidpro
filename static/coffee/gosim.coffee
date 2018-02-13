window.simulation = false
moving_sim = false
level_classes = {"I": "iinfo", "W": "iwarn", "E": "ierror"}

$("#show-simulator").hover ->
  if not moving_sim
    $(this).stop().animate {width: '110px'}, 200, "easeOutBack", ->
      $(this).find('.message').stop().fadeIn('fast')
, ->
  if not moving_sim
    $(this).find('.message').hide()
    $(this).stop().animate { width: '40px'}, 200, "easeOutBack", ->


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


simStart = ->



  # console.log(startRequest)

  $.post('/engine/flow/start', JSON.stringify(startRequest)).done (results) ->
    window.simResults(results)

window.simResults = (results) ->
  console.log(results)

# refresh the simulator
$(".simulator-refresh").on "click", ->
  simStart()

# toggle simulator
$("#toggle-simulator").on "click", ->
  console.log("Toogle!")
  if not $("#simulator").is(":visible")
    showSimulator()
  else
    hideSimulator()