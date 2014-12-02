# ----------------------------------------
# Our globals
# ----------------------------------------

# chrome hack
document.onselectstart = -> return false

typeIsArray = Array.isArray || ( value ) -> return {}.toString.call( value ) is '[object Array]'

top = @

dirty = false

lastSaved = null

active_call = false
call_id = null

getCategory = (rule) ->
  if rule.category
    if window.base_language
      return rule.category[window.base_language]
    return rule.category

opNames =
  'lt': '< '
  'gt': '> '
  'eq': ''
  'between': ''
  'number': ''
  'starts': ''
  'contains': ''
  'contains_any': ''
  'date': ''
  'date_before': ''
  'date_equal': ''
  'date_after': ''
  'regex': ''

targetDefaults =
  anchor: [ "Continuous", { faces:["top", "left", "right"] }]
  endpoint: [ "Rectangle", { width: 20, height: 20, hoverClass: 'endpoint-hover' }]
  hoverClass: 'target-hover'
  dropOptions: { tolerance:"touch", hoverClass:"drop-hover" }
  dragAllowedWhenFull:true
  isTarget:true

actionSource =
  anchor: "BottomCenter"
  maxConnections:1
  scope:'rules'
  isSource:true

ruleSource =
  anchor: "BottomCenter"
  maxConnections:1
  scope:'actions'
  isSource:true

markDirty = ->
  if not $("#workspace").data('loading')
    dirty = true

repaint = (delay = 200) ->
  window.setTimeout(jsPlumb.repaintEverything, delay)

wsOffset = $("#workspace").offset()

ruleDragMessage = "To send back a reply <span class='attn'>drag</span> the red box."
actionDragMessage = "To save responses to this message <span class='attn'>drag</span> the red box."

getResponseModal = (node, removeOnCancel=false) ->
  if window.is_voice
    return new KeypadResponseModal(node, removeOnCancel)
  else
    return new SMSResponseModal(node, removeOnCancel)

updateActivity = ->
  if $("#workspace").data('dragging')
    return

  url = activityURL
  if window.simulation and not window.is_voice
    return

  url += "?"
  if active_call
    url += "&flow=1"

  if call_id
    url += "&call=" + call_id

  if window.simulation
    url += "&simulation=true"

  $.ajax(
    type: "GET"
    url: url
    cache: false
    success: (data) ->
      updateCallStatus(data['call'])
      updateFlowDetails(data['flow'])
      updatePendingStart(data['pending'])

      if window.is_voice and window.simulation
        if $(".simulator-body").text() != "One moment.."
          window.updateSimulator(data)

      if $("#workspace").data('dragging')
        return
      for node in $('#workspace').children('.node')
        node = $(node).data('object')
        node.setActivity(data)

      setTimeout(updateActivity, 5000)
    error: (status) ->
      console.log("Error:")
      console.log(status)
  )

updateCallStatus = (call) ->
  if active_call
    status = $('.call-status')

    if call.status == 'P'
      status.find('.status').text('Calling..')
      status.find('.icon-phone').css('display', 'inline-block')
      status.find('.duration').text('')
    else if call.status == 'D' or call.status == 'F'
      status.find('.duration').text('')
      status.find('.status').text('Select a message to record')
      status.find('.icon-phone').hide()
      $('.recording').each -> $(this).removeClass('recording')
      call_id = null
    else if call.status == 'I'
      status.find('.icon-phone').css('display', 'inline-block')
      status.find('.status').text('Recording messages..')
      # status.find('.duration').text(call.duration)

updateFlowDetails = (flow) ->
  if flow.action_sets
    for as in flow.action_sets
      for action in as.actions
        if action.type = 'say' and action.uuid
          update = $('#' + action.uuid).data('object')
          if update
            update.initialize(action)
            update.node.invalidate()
          else
            console.log("Couldn't find: " + action.uuid)

updatePendingStart = (status) ->
  if status == 'S' or status == 'P'
    $("#pending").show()
  else
    $("#pending").hide()

updateSimulatorButton = (json) ->
  if (window.flowName and window.flowName.indexOf("Sample Flow") == 0) or (json.action_sets.length == 2 and json.rule_sets.length == 1)
    $('#show-simulator').stop().delay(1000).animate {width: '110px'}, 200, "easeOutBack", ->
      $(this).find('.message').stop().fadeIn('fast')
  else
    $('#show-simulator').find('.message').hide()
    $('#show-simulator').stop().animate { width: '40px'}, 200, "easeOutBack", ->

isNumber = (num) ->
  !isNaN(parseFloat(num)) && isFinite(num)

# load our flow from the db
initialize = (json) ->

  start = new Date().getTime()
  jsPlumb.doWhileSuspended ->
    $("#workspace").data("loading", true)

    window.base_language = json.base_language

    nodes = []
    elements = []
    i = 0

    # our actions nodes
    for actionset in json.action_sets
      node = new ActionsNode({left: actionset.x, top:actionset.y}, actionset.uuid)

      if actionset.uuid == json.entry
        node.setRoot(true, false)

      for action in actionset.actions
        if action.type == 'reply'
          a = new SendResponseAction(node)
        else if action.type == 'send'
          a = new SendMessageAction(node)
        else if action.type == 'say'
          a = new SayAction(node)
        else if action.type == 'add_group'
          a = new AddToGroupAction(node)
        else if action.type == 'del_group'
          a = new RemoveFromGroupAction(node)
        else if action.type == 'email'
          a = new SendEmailAction(node)
        else if action.type == 'api'
          a = new APIAction(node)
        else if action.type == 'save'
          a = new SaveToContactAction(node)
        else if action.type == 'flow'
          a = new StartFlowAction(node)
        else if action.type == 'trigger-flow'
          a = new TriggerFlowAction(node)
        else if action.type == 'lang'
          a = new SetLanguageAction(node)

        if a
          a.initialize(action)
          node.addAction(a)

      nodes[i] = node
      elements[i++] = node.getElement()

      node.setDestination(actionset.destination)

    # our rules nodes
    for ruleset in json.rule_sets
      node = new RulesNode({left:ruleset.x, top:ruleset.y}, ruleset.uuid)

      if ruleset.uuid == json.entry
        node.setRoot(true, false)

      nodes[i] = node
      elements[i++] = node.getElement()

      if ruleset.label
        node.setLabel(ruleset.label)

      if ruleset.webhook
        node.setWebhook(ruleset.webhook, ruleset.webhook_action, false)

      if ruleset.operand
        node.setOperand(ruleset.operand)

      if ruleset.finished_key
        node.setFinishedKey(ruleset.finished_key)

      rules = []
      destinations = {}

      for r in ruleset.rules

        if r.destination
          key = getCategory(r)

          if key
            key = key.toLowerCase()

          destinations[key] = r.destination

        operandTwo = null
        if r.test.type in ['between', 'date_between']
          operand = r.test.min
          operandTwo = r.test.max
        else
          operand = r.test.test

        rules.push
          id: r.uuid
          operand: operand
          operandTwo: operandTwo
          operator: r.test.type
          category: r.category

      node.setRules(rules)
      node.setDestinations(destinations)

    # if we have a single node with no message, don't bother
    if elements.length == 1
      root = elements[0].data('object')
      if root instanceof ActionsNode
        actions = root.getActions()
        if actions.length == 1
          action = actions[0]
          if action instanceof SendResponseAction
            if not action.getMessage() or action.getMessage().strip().length == 1
              elements = []
              nodes = []

    if json.metadata and json.metadata.notes
      for note_spec in json.metadata.notes
        note = new Note()
        note.initialize(note_spec)

    $("#workspace").append(elements)

    for node in nodes
      node.wireConnections()
      if canEdit
        node.enableDrop()
        node.enableDrag()

      # make sure our workspace grows properly
      node.moveTo(node.offset, false)

  $("#workspace").data("loading", false)
  console.log("Loaded (" + (new Date().getTime() - start) + "ms)");

  # after loading the flow, lets call
  updateSimulatorButton(json)

  # repaint()


  lastSaved = json.last_saved
  determineDragHelper()
  #note = new Note()
  #note.setTitle("Title")
  #note.setBody("This flow demonstrates looking up an order using a webhook and giving the user different options based on the results.  After looking up the order the user has the option to send additional comments which are forwarded to customer support representatives.\n\nUse order numbers CU001, CU002 or CU003 to see the different cases in action.")
  #note.setBody("This single question poll demonstrates how TextIt can easily help measure what is happening in the field.")
  #note.setBody("This flow demonstrates a simple customer satisfaction survey that rewards completers with a unique coupon generated by a webhook.")
  #note.setBody("You can use actions and clever routing in your flows to build complex applications.\n\nThis advanced flow creates an SMS \"chat room\". After joining, any message sent will be forwarded to the others in the room. They can change their name by sending \"nick [name]\" and they can exit the room by sending \"exit\".\n\nTo start using this flow, <a href='/trigger'>create a trigger</a> with the keyword \"join\" to start the flow.")

setCallActive = (active) ->
  active_call = active

  if call_id
    # initiate the call
    url = '/handle/' + call_id + '/?hangup=1'
    call_id = null

    $.ajax({
      type: "POST",
      url: url,
    }).done((data) ->
      # console.log(data)
    )

  if active
    $("html > body").addClass('call')
    jsPlumb.toggleDraggable($('.node:visible, ._jsPlumb_endpoint'))
  else
    $('.recording').each -> $(this).removeClass('recording')
    $("html > body").removeClass('call')
    jsPlumb.toggleDraggable($('.node:visible, ._jsPlumb_endpoint'))

  $('.node:visible').each ->
    jsPlumb.recalculateOffsets($(this))
    jsPlumb.repaint($(this))

toast = (message, duration=2000) ->

  console.log(message)

  offset = $("#workspace").data('mouse')
  ele = $(".toast")
  ele.find('.message').html(message)
  ele.css({left:offset.left+40, top: offset.top - 20 })
  ele.fadeIn()
  window.setTimeout (-> ele.fadeOut()), duration



connect = (sourceId, targetId, scope, fireEvent = true) ->

  root = $(".root")
  if root.hasClass('initial')
    root.removeClass('initial')

  existing = jsPlumb.getEndpoints(targetId)
  if existing and existing.length > 0 and not existing[0].connections
    targetPoint = existing[0]
  else
    targetPoint = jsPlumb.addEndpoint(targetId, { scope: scope }, targetDefaults)

  unless canEdit
    targetPoint.setEnabled(false)

  if jsPlumb.getConnections({source:sourceId, scope:'actions'}).length == 0
    # console.log(" connecting " + sourceId + " -> " + targetId)
    jsPlumb.connect({ editable:true, source: sourceId, target: targetPoint, fireEvent: fireEvent, deleteEndpointsOnDetach:true})

determineDragHelper = ->

  # see if we should show our drag helper
  actionNodes = $("#workspace").find('.node.actions')

  if actionNodes.length == 1
    actionNode = $(actionNodes[0]).data('object')
    if actionNode.hasSend() and not actionNode.getConnection()
      showDragHelper(actionNode.source.offset(), actionDragMessage)
      return

  ruleNodes = $("#workspace").find('.node.rules')
  if ruleNodes.length == 1
    ruleNode = $(ruleNodes[0]).data('object')
    rules = ruleNode.getRules()
    if rules.length == 1 and not rules[0].getConnection()
      if ruleNode.getConnectionsIn().length > 0
        showDragHelper(rules[0].source.offset(), ruleDragMessage)

isValidUrl = (url) ->
  return /((([A-Za-z]{3,9}:(?:\/\/)?)(?:[-;:&=\+\$,\w]+@)?[A-Za-z0-9.-]+|(?:www.|[-;:&=\+\$,\w]+@)[A-Za-z0-9.-]+)((?:\/[\+~%\/.\w-_]*)?\??(?:[-\+=&;%@.\w_]*)#?(?:[\w]*))?)/.test(url)

showSplitOperandDialog = (node) ->
  hideDragHelper()

  form = $('.forms > .update-variable').clone()

  modal = new ConfirmationModal(gettext('Rule Variable'))
  modal.addClass('operand-dialog')
  modal.setForm(form)
  input = form.find('input.split-operand')

  if node.operand
    input.val(node.operand)
  else
    input.val('@step.value')

  initAtMessageText(form.find('.split-operand'), node.getFlowVariables([], {}, true))

  # TODO: go back tot he right modal type here based on ivr
  modal.setListeners
    onPrimary: ->
      ele = form.find('.split-operand')
      node.setOperand(ele.val().strip())
      modal.dismiss()
      node.markDirty()
      dialog = getResponseModal(node)
      dialog.selectTab('multiple')
      dialog.show()
    onSecondary: ->
      modal.dismiss()
      dialog = getResponseModal(node)
      dialog.selectTab('multiple')
      dialog.show()

  modal.show()


showWebhookDialog = (node) ->

  hideDragHelper()

  form = $('.forms > .api').clone()

  if node.webhook
    form.find('input').val(node.webhook)

  if not node.webhook_action
    node.webhook_action = 'GET'

  form.find('.step-uuid').text(node.getId())
  form.find('.form-action').val(node.webhook_action).select2
    data: [ {id:'GET', text:'GET'}, {id:'POST', text:'POST'} ]
    minimumResultsForSearch: -1

  modal = new ConfirmationModal(gettext('Call Webhook'))
  modal.addClass('api')
  modal.addClass('webhook-dialog')
  modal.setForm(form)
  modal.setListeners
    onBeforePrimary: (modal) ->
      url = form.find('input.url').val().strip()

      if url.length > 0
        valid = isValidUrl(url)
        if not valid
          form.find('.invalid-url').show()
          form.find('input.url').focus().select()
          return true
      return false

    onPrimary: ->
      url = form.find('input.url').val().strip()
      action = form.find('input.form-action').val().strip()
      node.setWebhook(url, action)
      modal.dismiss()
      node.invalidate()

    onSecondary: ->

  modal.show()

showActionDialog = (action) ->

  if active_call
    return

  hideDragHelper()
  modal = new ConfirmationModal(action.getTitle(), action.getInstructions())
  modal['action'] = action
  form = action.createForm(modal)
  modal.setForm(form)
  action.initializeForm(modal.getForm())

  modal.addClass(action.getName())
  modal.addClass('action-dialog')
  modal.setIcon(action.getIcon())

  tertiary = action.getTertiary()

  if tertiary
    modal.setTertiaryButton tertiary.name, tertiary.handler

  modal.setListeners
    onBeforePrimary: (modal) ->
      return modal['action'].validate(modal.getForm())
    onPrimary: (modal) ->
      form = modal.getForm()
      action = modal['action']
      action.submitForm(form)
      action.attach()
      action.node.checkSource()
      markDirty()
      determineDragHelper()
      updateSimulatorButton(asJSON())
    onSecondary: (modal) ->
      determineDragHelper()
      action.cancel()

  modal.keyboard = false
  modal.show()
  action.onShow(modal.getForm())

adjustRootNode = ->

    topNode = null
    topOffset = null

    nodes = $("#workspace > .node")
    for node in nodes
      node = $(node)
      if node.hasClass('root')
        node.data('object').setRoot(false)
      offset = node.offset()
      if not topNode or offset.top < topOffset.top
        topNode = node
        topOffset = offset
      else if offset.top == topOffset.top and offset.left < topOffset.left
        topNode = node
        topOffset = offset

    if topNode
      topNode.data('object').setRoot(true)


save = ->

  # don't save if we are triggered while loading
  if $("#workspace").data("loading")
    return

  # already saving?  try again in 1000 millis
  if $("#workspace").data("saving") or $("#workspace").data("dragging")
    window.setTimeout((-> dirty = true), 1000)
    return

  dirty = false

  console.log("Saving " + new Date().getTime())
  # jsPlumb.repaintEverything()

  $("#workspace").data("saving", true)

  json = asJSON()

  console.log(json)

  # partially hide the run simulator
  updateSimulatorButton(json)

  # console.log(JSON.stringify(json))
  $("#error").hide();
  $("#saving").show();

  $.ajax({
    type: "POST",
    url: postURL,
    data: JSON.stringify(json),
    dataType: "text json",
  }).done(->
    window.setTimeout((-> $("#error").hide();$("#saving").hide()), 1000))
    .done((data, status, xhr) ->
        if data.status == 'unsaved'
          date = new Date(Date.parse(data.saved_on))

          message = data.saved_by + gettext(" is currently editing this Flow. Your changes will not be saved until the Flow is reloaded.")
          modal = new Modal(gettext('Editing Conflict'), message)
          modal.addClass('alert')
          modal.setPrimaryButton(gettext('Reload'))
          modal.setListeners
            onPrimary: ->
              location.reload()
              modal.dismiss()
          modal.show()
        else:
          lastSaved = data.saved_on
        )
    .fail((xhr, status, error) -> $("#saving").hide();$("#error").show())
    .always( ->
      $("#workspace").data("saving", false))

# ----------------------------------------
# Some global methods
# ----------------------------------------
jsPlumb.importDefaults
  DragOptions : { cursor: 'pointer', zIndex:2000 }
  DropOptions : { tolerance:"touch", hoverClass:"drop-hover" }
  Endpoint: "Blank"
  EndpointStyle: { strokeStyle: "transparent" }
  PaintStyle: { lineWidth:5, strokeStyle:"#98C0D9" }
  HoverPaintStyle: { strokeStyle: "#27ae60"}
  HoverClass: "connector-hover"

  Connector:
    [ "Flowchart",
        stub: 12
        midpoint: .85
        alwaysRespectStubs:true
        gap:[0,7]
        cornerRadius: 2
        #events:
        #  click: (connection, evt) ->
        #    console.log("Connection clicked")
    ]

  ConnectionOverlays : [
    ["PlainArrow", { location:.9999, width: 12, length:12, foldback: 1 }],
    [ "Label",
      label:"3,418"
      location:25
      cssClass:'label'
      events:
        click: (label, evt) ->
          evt.preventDefault()
          evt.stopPropagation()
          return false
    ]
  ]

  Container: "workspace"
  RenderMode: "svg"

# get the node for an id
getNodeById = (id) ->
  return $("#" + id).data('object')

# find the parent node object given any jquery decendant in its tree
getNode = (ele) ->
  node = $(ele).parents('.node')
  if node
    return node.data('object')

# find the parent rule object given any jquery decendant in its tree
getRule = (ele) ->
  rule = $(ele).parents('.rule')
  if rule
    return rule.data('object')

dragHelperId = undefined
showDragHelper = (sourceOffset, message) ->

  helper = $('#drag-helper')
  helpText = helper.find('.help-text')

  helper.css('opacity', 0)
  helpText.css('opacity', 0).css('left', -10)
  helper.show()

  if message
    helper.find('.help-text').html(message)

  helper.offset({left:sourceOffset.left - 8, top: sourceOffset.top - 20})
  helper.animate {top: sourceOffset.top + 14, opacity: 1}, complete: ->
    helper.find('.help-text').animate {left: 30, opacity: 1}, duration: 200, complete: ->
      if dragHelperId
        clearTimeout(dragHelperId)
        dragHelperId = undefined

      dragHelperId = setTimeout ( ->
        helper.fadeOut()
      ), 20000

hideDragHelper = ->
  $('#drag-helper').fadeOut()
  if dragHelperId
    clearTimeout(dragHelperId)
    dragHelperId = undefined

onBoxDrag = (evt, ui) ->
  ele = ui.helper
  node = ele.data('object')
  offset = ele.offset()
  newOffset =
    left: offset.left -= wsOffset.left
    top: offset.top -= wsOffset.top
  node.moveTo(newOffset)

class Clone

  @dirty = false
  constructor: (@template, @id = uuid()) ->

  getElement: ->
    if not @element
      @element = $('#templates > ' + @template).clone()
      @element.attr('id', @id)
      @element.hide()
      @element.data('object', @)
      @initializeElement(@element)

    if @dirty
      @element.attr('id', @id)
      @element.data('object', @)
      @updateElement(@element)
      @dirty = false

    return @element

  markDirty: ->
    if not @element
      @dirty = true
    else
      @updateElement(@element)

  # called after the element is first created
  initializeElement: (element) ->

  # called right before the element is fetched but only if it's dirty
  updateElement: (element) ->

  getId: ->
    return @id

  setId: (@id) ->
    @markDirty()


# ----------------------------------------------------------------------
# A SimulatorNode represents the real phone messaging based to the flow
# ----------------------------------------------------------------------
class SimulatorNode extends Clone

  constructor: ->
    super('.simulator', 'simulator')
    @getElement().show()

  appendToWorkspace: ->
    offset = $('#workspace').offset()
    offset['left'] = 400
    $('#workspace').offset(offset)
    offset['left'] = -400
    $('simulation-space').offset(offset)
    $('#simulation-space').append(@getElement())


class Note extends Clone

  constructor: ->
    super('.sticky')

    ele = @getElement()
    $('#workspace').append(ele)
    if canEdit
      ele.draggable(
        containment: 'parent'
        start: -> hideDragHelper()
        stop: -> markDirty()
      ).fadeIn()
    else
      ele.find('.close').hide()
      ele.fadeIn()

    ele.dblclick (e) ->
      e.stopPropagation()

    unless canEdit
      ele.find('input, textarea').prop('disabled', true)

    note = @
    ele.find('input, textarea').keyup (e) ->
      note.lastChange = new Date().getTime()
      note.checkQuietPeriod()
    .focus (e) ->
        input = $(this)
        if input.val() == '...' or input.val() == gettext('Note Title')
          input.val('')
    .blur (e) ->
        input = $(this)
        if input.val() == ''
          if input.hasClass('body')
            input.val('...')
          else
            input.val(gettext('Note Title'))


    ele.find('.close').on 'click',  ->
      unless canEdit
        return
      ele.remove()
      markDirty()

  checkQuietPeriod: () ->
    if @pendingQuiet
      window.clearTimeout(@pendingQuiet)
    note = @
    @pendingQuiet = window.setTimeout( ->
      if new Date().getTime() - note.lastChange > 1000
        markDirty()
    , 2000)

  initialize: (json) ->
    @setTitle(json.title)
    @setBody(json.body.replace(/<(?:.|\n)*?>/gm, ''))
    @getElement().css({left: json.x, top:json.y })
    @getElement().find('textarea').autosize()


  setTitle: (@title) ->
    @getElement().find('.title').val(@title)

  setBody: (@body) ->
    if @body
      #body = @body.split('\n').join('<br>')
      @getElement().find('.body').val(@body)

  getTitle: () ->
    return @getElement().find('.title').val()

  getBody: () ->
    @getElement().find('.body').val()


  asJSON: ->
    offset = @getElement().offset()
    return { title: @getTitle(), body: @getBody(), x:offset.left - wsOffset.left, y:offset.top - wsOffset.top}

# ----------------------------------------------------------
# A WorkspaceNode represents a building block on the canvas
# ----------------------------------------------------------

nodeCounter = 0
class WorkspaceNode extends Clone

  @dragstart = {}

  constructor: (@offset, template, id) ->
    super(template, id)
    ele = @getElement()
    ele.addClass('node_' + nodeCounter++)
    ele.show()
    @moveTo(offset)

  getScope: -> "defaultScope"
  enableDrag: ->
     # the second parameter are jQuery UI draggable pass-throughs
    jsPlumb.draggable($('#' + @getId()), {
      containment: "parent"
      cancel: '.source'

      # store our start position, we might have to move ourselves back
      start: (evt, ui) ->
        hideDragHelper()
        $(this).data('last', $(this).offset())
        $("#workspace").data('dragging', true)

      # while we drag, detect collisions with other boxes
      drag: (evt, ui) -> onBoxDrag(evt, ui)

      # on stop drag, see if we need to be moved back to our original position
      stop: (evt, ui) ->
        node = $(this).data('object')
        ele = node.getElement()
        if ele.hasClass('collides')
          last = ele.data('last')
          last.left -= wsOffset.left
          last.top -= wsOffset.top
          node.moveTo(last)
          node.invalidate()
        else
          offset = node.element.offset()
          node.moveTo({
            left: offset.left -= wsOffset.left
            top: offset.top -= wsOffset.top
          })
        $("#workspace").data('dragging', false)
        markDirty()
    })

  appendToWorkspace: ->
    $('#workspace').append(@getElement())

  setRoot: (root, invalidate=true) ->
    if root
      @getElement().addClass('root')
    else
      @getElement().removeClass('root')

    if invalidate
      @invalidate()

  setInitial: (initial) ->
    if initial
      @getElement().addClass('initial')
    else
      @getElement().removeClass('initial')

  onCreate: ->
    # no-op

  checkForCollisions: ->
    id = @getId()
    collides = false
    children = $("#workspace").children('.node')
    for otherElement in children
      if otherElement.id != id
        if @collides($(otherElement))
          collides = true
          break

    if collides
      @getElement().addClass('collides')
    else
      @getElement().removeClass('collides')

    return @



  collides: (other) ->

      otherOffset = other.offset()
      ourOffset = @getElement().offset()

      ourBox =
        left: ourOffset.left
        top: ourOffset.top
        bottom: @getElement().outerHeight() + ourOffset.top
        right: @getElement().outerWidth() + ourOffset.left

      otherBox =
        left: otherOffset.left
        top: otherOffset.top
        bottom: other.outerHeight() + otherOffset.top
        right: other.outerWidth() + otherOffset.left

      if ourBox.bottom < otherBox.top
        return false
      if ourBox.top > otherBox.bottom
        return false
      if ourBox.left > otherBox.right
        return false
      if ourBox.right < otherBox.left
        return false
      return true

  isGhost: ->
    return @ghost

  setGhost: (isGhost) ->
    @ghost = isGhost
    if isGhost
      @getElement().addClass('ghost')
    else
      @getElement().removeClass('ghost')

  getAproximateHeight: ->
    return 100

  moveTo: (offset, checkRoot=true) ->

    $('#header, #footer').width($(document).width());

    ele = @getElement()
    ele.css(offset)

    #@invalidate()
    @checkForCollisions()

    if checkRoot and not ele.hasClass('ghost')
      adjustRootNode()

    ws = $("#workspace")
    bottom = ws.height()
    nodeHeight = @getAproximateHeight()
    newBottom = offset.top + Math.max(nodeHeight, 700)

    if newBottom > bottom
      ws.css('height', newBottom)

  remove: ->
    if @element
      # the destinations on rules might still reference us but we'll
      # remove those lazily since only visible connections are serialized
      jsPlumb.remove(@element)
      updateSimulatorButton(asJSON())
      determineDragHelper()

  setActivity: (data) ->
    activity = data.activity
    count = activity[@getId()]

    if count != undefined
      count = count.count
    else
      count = 0

    activityContainer = @getElement().find('.activity').first()
    if count > 0
      activityContainer.text(count).fadeIn()
    else
      activityContainer.hide()

  invalidate: ->
    if @element
      try
        # console.log("Recalculating:")
        # console.log(@)
        jsPlumb.recalculateOffsets(@element)
        jsPlumb.repaint(@element)
      catch error
        console.log(@element)
        console.log("Invalidate Failed: " + error)
        console.log(error)

  asJSON: ->
    offset = @getElement().offset()
    return { uuid:@getId(), x:offset.left - wsOffset.left, y:offset.top - wsOffset.top }

  getFlowVariables: (nodes=[], variables={}, skipFlow=false) ->

    if @ not in nodes
      @addFlowVariables(variables, skipFlow)

    nodes.push(@)

    parents = @getParents()
    for p in parents
      if p not in nodes
        p.getFlowVariables(nodes, variables)

    result = []
    for k,v of variables
      result.push({name:k, display:v})
    return result

class ActionsNode extends WorkspaceNode

  constructor: (offset, id) ->
    super(offset, '.actions', id)
    @source = @getElement().find('.source')

    node = @
    @source.on 'click', (evt) ->
      evt.stopPropagation()
      unless canEdit
        return

      connection = node.getConnection()
      if connection
        hideDragHelper()
        modal = new ConfirmationModal(gettext('Remove'), gettext('Are you sure you want to remove this connection?'))
        modal.addClass('alert')
        modal.setListeners
          onPrimary: ->
            jsPlumb.detach(connection)
            markDirty()
        modal.show()
      else
        showDragHelper(node.source.offset(), actionDragMessage)

    jsPlumb.makeSource(@source, actionSource)

  getScope: -> "rules"

  replaceAction: (action) ->
    actionList = @getElement().find('.actions')
    ele = action.getElement()
    ele.show()

    old = actionList.find('#' + action.getId()).data('object')
    if old != action
      old.getElement().replaceWith(ele)

  addFlowVariables: (variables) ->
    if @hasWebhook()
      variables['extra'] = 'Webhook variables'

    if @hasSend()
      variables['step'] = gettext('Contact Response')
      variables['step.value'] = gettext('Contact Response')
      for contactVariable in window.message_completions
        variables['step.' + contactVariable.name] = contactVariable.display

  addAction: (action) ->
    actionList = @getElement().find('.actions')
    actionList.append(action.getElement())
    action.getElement().show()
    action.setAttached(true)
    @actionCount = actionList.children().length
    @checkSource()

  removeAction: (action) ->
    node = @
    action.element.remove()
    actionList = node.getElement().find('.actions')
    node.actionCount = actionList.children().length
    node.checkSource()

  hasActions: ->
    return @actionCount > 0

  getAproximateHeight: ->
    if not @actionCount
      @actionCount = 1
    return @actionCount * 150

  setDestination: (@destination) ->

  wireConnections: ->
    if @destination != null
      connect(@getSourceId(), @destination, 'rules')
    unless canEdit
      jsPlumb.setSourceEnabled(@source, false)


  onCreate: ->
    #if window.is_voice
    action = @getElement().find('.action.send-response:first,.action.say').data('object')
    #else
    #  action = @getElement().find('.action.send-response:first').data('object')
    showActionDialog(action)

  createTargetNode: () ->
    new RulesNode($("#workspace").data("mouse"), uuid())

  getSourceId: ->
    @source.attr('id')

  enableDrop: ->
    jsPlumb.makeTarget($("#" + @getId()), { scope: 'actions' }, targetDefaults)

  #remove: ->
  #  jsPlumb.detachAllConnections(@getSourceId());
  #  jsPlumb.detachAllConnections(@getId());
  #  jsPlumb.removeAllEndpoints(@getSourceId())
  #  jsPlumb.removeAllEndpoints(@getId())
  #  jsPlumb.unmakeSource(@getSourceId(), false);
  #  jsPlumb.remove(@getElement())

  hasWebhook: ->
    return @getElement().find('.actions > .action.api').size() > 0

  hasSend: ->
    if window.is_voice
      sends = @getElement().find('.actions > .action.say')
    else
      sends = @getElement().find('.actions > .action.send-response')
    for send in sends
      if $(send).data('object').getMessage()
        return true
    return false

  checkSource: ->
    hasSend = @hasSend()
    jsPlumb.setSourceEnabled(@source, hasSend)
    if hasSend
      @source.removeClass('source-disabled')
    else
      @source.addClass('source-disabled')
      jsPlumb.detachAllConnections(@source)

  setActivity: (activity) ->
    super(activity)
    visited = activity.visited
    connection = @getConnection()

    if visited and @destination
      key = @getId() + '->' + @destination
      if key of visited
        count = visited[key]
    if count != undefined
      count = count.count
    else
      count = 0

    if connection
      overlays = connection.getOverlays()
      if overlays and overlays.length > 1
        # our second overlay is the actual label
        if count > 0
          overlays[1].setLabel("" + count)
          overlays[1].show()
        else
          overlays[1].hide()

  getConnection: ->
    connections = jsPlumb.getConnections({
      source: @source.attr('id'),
      scope: 'rules'
    });

    if connections and connections.length > 0
      return connections[0]

  getParents: ->
    connections = jsPlumb.getConnections({
      scope: 'actions'
      target: @getElement()
    });
    return ($($(c.source).parents('.node')).data('object') for c in connections)

  getActions: ->
    return ($(ele).data('object') for ele in @getElement().find('.action'))

  asJSON: ->

    json = super()
    actions = []
    for action in @getActions()
      actions[actions.length] = action.asJSON()

    connection = @getConnection()

    json['actions'] = actions

    if connection
      json['destination'] = connection.targetId
    else
      json['destination'] = null

    return json

class RulesNode extends WorkspaceNode
  constructor: (offset, id) ->
    super(offset, '.node.rules', id)

    category = gettext("All Responses")
    if window.base_language
      cat = {}
      cat[window.base_language] = category
      category = cat

    rules = [
      id: uuid()
      operator: "true"
      operand: "true"
      category: category
    ]

    @setRules(rules)

  onCreate: ->

    labels = []
    for node in $("#workspace").children('.node.rules')
      node = $(node).data('object')
      if not node.isGhost()
        label = node.getLabel()
        if label
          labels.push(label)

    count = labels.length + 1
    label = gettext("Response") + " " + count
    determineDragHelper()

    while label in labels
      label = gettext("Response") + " " + count
      count++

    @setLabel(label)

    modal = new getResponseModal(@, true)
    modal.show()
    modal.ele.find('#label-name').select().focus()

    # @element.find('.ruleset').trigger('click')

  getScope: -> "actions"

  setDestination: (destination) ->

  setFinishedKey: (@finishedKey) ->

  addRule: (id=uuid()) ->
    r = new Rule(@, id)
    return r

  addFlowVariables: (variables, skipFlow=false) ->
    slug = @getSlug()

    if @webhook
      variables['extra'] = gettext('Webhook variables')

    for contactVariable in window.message_completions
      variables[contactVariable.name] = contactVariable.display

    if not skipFlow
      variables['flow.' + slug] = @label
      variables['flow.' + slug + '.category'] = @label + " " + gettext("Category")
      variables['flow.' + slug + '.text'] = @label + " " + gettext("Text")
      variables['flow.' + slug + '.time'] = @label + " " + gettext("Time")

  setLabel: (label) ->
    @label = label
    if label and label.strip().length > 0
      @getElement().find('.ruleset .name').html("<div class='node-label'>" + label + "</div>")
    else
      @getElement().find('.ruleset .name').text(gettext("Receive SMS"))

    # generate our slug
    @slug =@label.toLowerCase().replace(/[^a-z0-9]+/g, '_')

  setOperand: (@operand) ->

  setWebhook: (@webhook, @webhook_action='GET', invalidate=true) ->
    ele = @element.find('.webhook')
    truncated = @webhook

    if not truncated
      truncated = ""

    idx = truncated.indexOf "?"
    if idx > 0
      truncated = truncated.substring 0, idx

    ele.find('.url').text(truncated)

    if @webhook
      ele.show()
    else
      ele.hide()

    markDirty()
    if invalidate
      @invalidate()

  getLabel: ->
    return @label

  getSlug: ->
    return @slug

  hasLabel: ->
    return @label and @label.strip().length > 0

  getEverythingId: ->
    for rule in @getRules()
      if rule.operator == 'true'
        return rule.getId()

  getRules: ->
    element = @getElement()
    rules = ($(ele).data('object') for ele in element.find('.rule'))
    return rules

  setDestinations: (@destinations) ->

  getConnectionsIn: ->
    connectionsIn = []
    for c in jsPlumb.getConnections({ target: @element.attr('id'), scope: 'rules' })
      connectionsIn.push($(c.source).parents('.node').data('object'))
    return connectionsIn

  getConnectionsOut: ->
    connectionsOut = []
    for rule in @getRules()
      connection = rule.getConnection()
      if connection
        node = $(connection.target).data('object')
        if node not in connectionsOut
          connectionsOut.push(node)
    return connectionsOut

  getConnectedNodes: ->
    connections = @getConnectionsOut()
    connectIn = @getConnectionsIn()
    for node in connectIn
      if node not in connections
        connections.push(node)
    return connections


  getConnections: ->
    connections = {}
    for rule in @getRules()
      connection = rule.getConnection()
      if connection
        # get the old connections so we can rewire everything
        connections[getCategory(rule)] = connection.targetId
    return connections

  getParents: ->
    connections = jsPlumb.getConnections({
      scope: 'rules'
      target: @getElement()
    });
    return ($($(c.source).parents('.node')).data('object') for c in connections)


  setRules: (rules) ->

    jsPlumb.setSuspendDrawing(true, true)

    # remove old rules that aren't present in our new rules
    categories = {}
    for rule in @getRules()
      found = false
      i = -1
      for newRule in rules
        i++
        if rule.id == newRule.id
          rule.update(newRule)
          newRule['added'] = true
          found = true
          if getCategory(rule)
            key = getCategory(rule).toLowerCase()
            if key of categories
              rule.getElement().hide()
            else
              rule.getElement().show()
              categories[key] = rule
          break

      if !found
        rule.remove()

    # add the new rules
    elements = []
    i = 0

    for rule in rules
      if rule.added
        continue

      if rule.id
        r = new Rule(@, rule.id)
      else
        r = new Rule(@)

      r.update(rule)

      rule.id = r.getId()
      category = getCategory(rule)

      if category
        key = category.toLowerCase()
        if key of categories
          r.getElement().hide()
        else
          categories[key] = r

      elements[i++] = r.getElement()

    ruleList = @getElement().find('.rules')
    ruleList.append(elements)
    @getElement().find('tr > td:first').attr('colspan', @getRules().length)
    for rule in rules
      old = ruleList.find("#" + rule.id)
      ruleList.append(old)

    jsPlumb.setSuspendDrawing(false)


  wireConnections: (destinations = @destinations) ->

    # make sure all of our connections are still there
    for category, id of destinations
      if $('#' + id).length == 0
        delete destinations[category]
        delete @destinations[category]

    # finally, wire up our plubming
    for rule in @getRules()
      if not rule.getConnection()
        if rule.getElement().is(':visible')
          key = getCategory(rule)
          if key
            key = key.toLowerCase()
          if destinations
            targetId = destinations[key]
            if targetId
              connect(rule.getSourceId(), targetId, 'actions')
            unless canEdit
              jsPlumb.setSourceEnabled(rule.getSourceId(), false)

  remove: ->
    for rule in @getRules()
      rule.remove()
    super()
    updateSimulatorButton(asJSON())

    #jsPlumb.removeAllEndpoints(@getId())
    #jsPlumb.detachAllConnections(@getId())
    #@getElement().remove()

  enableDrop: ->
    jsPlumb.makeTarget($("#" + @getId()), { scope: 'rules' }, targetDefaults)

  getRulesWithCategory: (category) ->
    rules = []
    if category
      category = category.toLowerCase()
      for rule in @getRules()
        cat = getCategory(rule)
        if cat and cat.toLowerCase() == category
          rules.push(rule)
    return rules

  setActivity: (activity) ->
    super(activity)
    visited = activity.visited
    # set it on our rules
    for rule in @getRules()
      connection = rule.getConnection()
      if rule.getElement().is(':visible') and connection
        rules = @getRulesWithCategory(getCategory(rule))
        count = 0
        for commonRule in rules
          key = commonRule.getId() + '->' + connection.targetId
          if visited
            visitedCount = visited[key]
            if visitedCount != undefined
              count += visitedCount.count

        overlays = connection.getOverlays()
        if overlays and overlays.length > 1
          # our second overlay is the actual label
          if count > 0
            overlays[1].setLabel("" + count)
            overlays[1].show()
          else
            overlays[1].hide()

  asJSON: ->
    json = super()

    if @label
      json['label'] = @label

    if @webhook
      json['webhook'] = @webhook
      json['webhook_action'] = @webhook_action

    if @operand
      json['operand'] = @operand

    else
      json['operand'] = '@step.value'

    if @finishedKey
      json['finished_key'] = @finishedKey

    jsonRules = []
    for rule in @getRules()
      jsonRules[jsonRules.length] = rule.asJSON()

    # shared destinations for shared categories
    for jsonRule in jsonRules
      if not jsonRule.destination
        for otherRule in jsonRules
          if otherRule.destination and getCategory(otherRule) and getCategory(jsonRule)
            if getCategory(otherRule).toLowerCase() == getCategory(jsonRule).toLowerCase()
              jsonRule.destination = otherRule.destination

    json.rules = jsonRules
    return json


# ----------------------------------------
# Our Action class, wraps our jquery action
# ----------------------------------------
class Action extends Clone

  initialize: (action) -> console.log("Missing " + @constructor.name + ".initialize()")
  getTitle: -> gettext("Action")
  getInstructions: -> gettext("When somebody arrives at this point in your flow")
  getIcon: -> "icon-bubble-3"

  getName: ->
    return @template.substring(1)

  getTertiary: ->
    return null

  createForm: (modal) ->

    # initialize the right form according to our action
    form = $('#templates > .forms > ' + @template).clone()

    # select the current action in our drop down
    options = $('.action-options').find('select').clone()
    options.val(@getName())
    form.prepend(options)

    lastAction = @
    options.on 'change', ->
      modal.dismiss()
      type = options.val()
      if type == "add-to-group"
        action = new AddToGroupAction(lastAction.node)
      else if type == "remove-from-group"
        action = new RemoveFromGroupAction(lastAction.node)
      else if type == "send-message"
        action = new SendMessageAction(lastAction.node)
      else if type == "say"
        action = new SayAction(lastAction.node)
      else if type == "api"
        action = new APIAction(lastAction.node)
      else if type == "send-email"
        action = new SendEmailAction(lastAction.node)
      else if type == "send-response"
        action = new SendResponseAction(lastAction.node)
      else if type == 'save-to-contact'
        action = new SaveToContactAction(lastAction.node)
      else if type == 'start-flow'
        action = new StartFlowAction(lastAction.node)
      else if type == 'trigger-flow'
        action = new TriggerFlowAction(lastAction.node)
      else if type == 'lang'
        action = new SetLanguageAction(lastAction.node)

      if action
        action.setId(lastAction.getId())
        action.setAttached(lastAction.attached)
        showActionDialog(action)

    options.select2({minimumResultsForSearch: -1})

    form.find('input, textarea').on 'focus', ->
      $(this).removeClass('error')
    return form


  errorIfEmpty: (ele) ->
    length = 0
    select2 = ele.data('select2')
    if select2
      length = select2.val().length
    else
      length = ele.val().strip().length
    if length == 0
      ele.addClass('error')
      return true

    return false

  validate: (form) -> false
  initializeForm: (form) ->
  onShow: (form) ->
  setAttached: (@attached) ->

  moveup: ->
    prev = @element.prev()
    current = @element
    if prev.hasClass('action')
      prev.slideUp 200, ->
        prev.before(current)
        prev.slideDown('fast')
        current.find('.move-up').hide()
        markDirty()

  attach: ->
    if @attached
      @node.replaceAction(@)
    else
      @node.addAction(@)

    @node.invalidate()
    actions = @node.getActions()
    if actions.length == 1 and actions[0]['ghost']
      actions[0]['ghost'] = false

  remove: ->
    @node.removeAction(@)

  cancel: ->
    actions = @node.getActions()
    if actions.length == 1 and actions[0]['ghost']
      @node.remove()

  getFlowVariables: ->
    flowVariables = {}
    flowVariables['channel'] = gettext('Sent to')
    flowVariables['channel.name'] = gettext('Sent to Name')
    flowVariables['channel.tel'] = gettext('Sent to Phone')
    flowVariables['channel.tel_e164'] = gettext('Sent to Phone - E164')
    flowVariables['step'] = gettext('Contact Response')
    flowVariables['step.value'] = gettext('Contact Response')
    for contactVariable in window.message_completions
      flowVariables['step.' + contactVariable.name] = contactVariable.display
      flowVariables[contactVariable.name] = contactVariable.display
    flowVariables['flow'] = gettext('All flow variables')

    for variable in @node.getFlowVariables()
      flowVariables[variable.name] = variable.display

    result = []
    for k,v of flowVariables
      result.push({name: k, display: v})

    return result

  asJSON: -> {}

class APIAction extends Action
  constructor: (@node) ->
    super('.api')
    @action = 'GET'


  initialize: (action) ->
    if action.webhook
      @webhook = action.webhook

    if action.action
      @action = action.action

  getTitle: -> gettext("Webhook")
  getIcon: -> "icon-cloud-upload"

  initializeForm: (form) ->
    super(form)

    if @webhook
      form.find('.url').val(@webhook)

    form.find('.form-action').val(@action).select2
      data: [ {id:'GET', text:'GET'}, {id:'POST', text:'POST'} ]
      minimumResultsForSearch: -1

    form.find('.step-uuid').text(@getId())


  validate: (form) ->
    form.find('.error-text').hide()

    error = false
    urlWidget = form.find('.url')
    if @errorIfEmpty(urlWidget)
      return true

    if not isValidUrl urlWidget.val()
      urlWidget.addClass('error')
      form.find('.error-text').fadeIn()
      return true

    return error

  submitForm: (form) ->
    @webhook = form.find('.url').val()
    @action = form.find('input.form-action').val()
    @markDirty()

  initializeElement: (ele) ->
    truncated = @webhook

    if not truncated
      truncated = gettext("Click to set Webhook URL")

    idx = truncated.indexOf "?"
    if idx > 0
      truncated = truncated.substring 0, idx
    ele.find('.body > .message').html("<span class='url'>" + truncated + "</span>")

    @node.invalidate()

  updateElement: (ele) ->
    @initializeElement(ele)

  asJSON: ->
    return { type: 'api', webhook: @webhook, action: @action }

class SendMessageAction extends Action
  constructor: (@node) ->
    super('.send-message')
    @groups = []
    @contacts = []
    @variables = []

  initialize: (action) ->


    @message = ""
    @msg = action.msg
    if window.base_language
      if @msg
        @message = @msg[window.base_language]
    else if @msg
      @message = @msg

    @groups = action.groups
    @contacts = action.contacts

    if action.variables
      @variables = action.variables

    @markDirty()

  getTitle: ->
    return gettext("Send SMS")

  initializeForm: (form) ->
    super(form)

    variables = @getFlowVariables()

    # create our omnibox and set the existing data if we have it
    recipients = omnibox(form.find('.recipients'), 'cg', { variables: variables })
    recipients = form.find('.recipients').data('select2')

    data = []
    for group in @groups
      data.push({ id:'g-' + group.id, text:group.name })

    for contact in @contacts
      data.push({ id:'c-' + contact.id, text:contact.name })

    for variable in @variables
      data.push({ id:variable.id, text:variable.id })

    recipients.data(data)

    # set our message and initialize counter
    form.find('textarea').text(@getMessage())
    initMessageLengthCounter(form.find('textarea[name="message"]'), form.find('#counter'))
    initAtMessageText(form.find('textarea[name="message"]'), @getFlowVariables())

    form.find('.recipients').on 'select2-open', ->
      $(this).removeClass('error')

  onShow: (form) ->
    form.find('textarea').focus().select()

  setMessage: (recipients, @message) ->

    if window.base_language
      if not @msg
        @msg = {}
      @msg[window.base_language] = @message

    @contacts = []
    @groups = []
    @variables = []

    for recipient in recipients
      if recipient.id[0] == 'g'
        @groups.push
          id:parseInt(recipient.id.substring(2)),
          name:recipient.text
      else if recipient.id[0] == 'c'
        @contacts.push
          id:parseInt(recipient.id.substring(2)),
          name:recipient.text
      else if recipient.id[0] == '@'
        @variables.push
          id:recipient.id,
          name:recipient.id
      else if recipient.id[0] == 'n'
        @contacts.push
          phone:recipient.id.substring(2)
          name:recipient.id.substring(2)

    @markDirty()

  getMessage: ->
    return @message

  validate: (form) ->

    error = false
    recipients = form.find('.recipients')
    if recipients.data('select2').data().length == 0
      recipients.addClass('error')
      error = true

    message = form.find('textarea')
    if message.val().strip().length == 0
      message.addClass('error')
      error = true

    return error

  submitForm: (form) ->
    message = form.find('textarea').val()
    recipients = form.find('.recipients').data('select2')
    @setMessage(recipients.data(), message)

  updateElement: (ele) ->
    ele.find('.body > .message').text(@message)

    children = []
    for group in @groups
      groupEle = $("<div class=\"selection omni-option omni-group\"/>")
      groupEle.text(group.name)
      children.push(groupEle)

    for contact in @contacts
      contactEle = $("<div class=\"selection omni-option omni-contact\"/>")
      contactEle.text(contact.name)
      children.push(contactEle)

    for variable in @variables
      variableEle = $("<div class=\"selection\">")
      variableEle.text(variable.id)
      children.push(variableEle)

    ele.find('.recipients').empty().append(children)
    ele.find('.to').show()
    @node.invalidate()

  asJSON: ->
    msg_json = {}
    if window.base_language
      msg_json = @msg
    else
      msg_json = @getMessage()

    return { type:'send', msg:msg_json, contacts:@contacts, groups:@groups, variables:@variables }

class AddToGroupAction extends Action
  constructor: (@node, template='.add-to-group') ->
    super(template)
    @groups = []

  getIcon: ->
    return "icon-users-2"

  getTitle: ->
    return gettext("Add to Group")

  initialize: (action) ->

    # backwards compat
    if action['group']
      @groups = [action['group']]
    else
      @groups = action['groups']

    @markDirty()

  initializeForm: (form) ->
    super(form)

    # create our omnibox and set the existing data if we have it
    ele = form.find('.group')
    init = []
    for g in @groups
      if typeof g == 'string'
        init.push
          id: g
          text: g
      else
        init.push
          id: 'g-' + g.id
          text: g.name

    if init.length > 0
      ele.val(JSON.stringify(init))

    omnibox(ele, 'g', { variables: @getFlowVariables(), createSearchChoice: @getSearchChoice() })

    form.find('.group').on 'select2-open', ->
      form.find('.group').removeClass('error')

    if init.length == 0
      window.setTimeout ->
        $(".modal-body .group").select2("val", "");
      , 50

  getSearchChoice: ->
    (term, data) ->
      if term.indexOf('@') != 0 and data.length == 0
        return { id: '[_NEW_] ' + term, text: term }

  setGroups: (@groups) ->
    @markDirty()

  validate: (form) ->
    group = form.find('.group')
    groups = group.data('select2').data()
    if groups.length == 0
      group.addClass('error')
      return true
    return false

  submitForm: (form) ->
    groups = form.find('.group').data('select2').data()

    new_groups = []
    for group in groups
      if group.id.indexOf('@') == 0
        group.text = group.id
      else if group.id.indexOf('[_NEW_]') == 0
        group.name = group.text.substring(14)
      else
        group.id = parseInt(group.id.substring(2))
        group.name = group.text

      if isNaN(group.id)
        new_groups.push(group.text)
      else
        new_groups.push(group)

    @setGroups(new_groups)

  updateElement: (ele) ->

    body = ele.find('.body').empty()
    for group in @groups
      name = group
      if typeof group != 'string'
        name = group.name
      g = $('<div/>', { class: 'group selection omni-option omni-group', text: name })
      body.append(g)

    @node.invalidate()

  asJSON: ->
    return { type:'add_group', groups: @groups }

class RemoveFromGroupAction extends AddToGroupAction
  constructor: (@node) ->
    super(@node, template='.remove-from-group')

  getTitle: ->
    return gettext("Remove from Group")

  # can't create when removing from groups
  getSearchChoice: ->

  asJSON: ->
    return { type:'del_group', groups: @groups }

  # should be the same as add, but with a differet asJSON

class SendResponseAction extends Action
  constructor: (@node) ->
    super('.send-response')

  setPlaceHolder: (@placeholder) ->

  initialize: (action) ->

    if action.msg
      @msg = action.msg
      if window.base_language
        @setMessage(@msg[window.base_language])
      else if @msg
        @setMessage(action.msg)

  getMessage: ->
    return @message

  setMessage: (@message) ->
    if window.base_language
      if not @msg
        @msg = {}
      @msg[window.base_language] = @message
    @markDirty()

  setEmptyMessage: (empty) ->
    @getElement().find('.body .empty').html(empty)

  updateElement: (element) ->
    element.find('.body > .message').text(@message)
    @node.invalidate()

  getTitle: ->
    return gettext("Send SMS")

  #getInstructions: ->
  #  return "Send an SMS in response to this contact's last message."

  initializeForm: (form) ->
    super(form)
    textarea = form.find('textarea').text(@getMessage())

    if @placeholder
      textarea.attr('placeholder', @placeholder)

    initMessageLengthCounter(form.find('textarea[name="message"]'), form.find('#counter'))
    initAtMessageText(form.find('textarea[name="message"]'), @getFlowVariables())

  onShow: (form) ->
    form.find('textarea').focus().select()

  validate: (form) ->
    message = form.find('textarea')
    if message.val().strip().length == 0
      message.addClass('error')
      return true
    return false

  submitForm: (form) ->
    @setMessage(form.find('textarea').val())
    # remove initial from the root node
    # this will enable the use to add actions on the root action node
    # TODO: There should be a more sane way to do it.
    $('.node.root').removeClass('initial')

  asJSON: ->
    if window.base_language
      return { type: 'reply', msg: @msg }
    else
      return { type: 'reply', msg: @getMessage() }

class SayAction extends Action

  constructor: (@node) ->
    super('.say')

  getIcon: -> "icon-phone"
  getTitle: -> gettext("Play Message")
  setPlaceHolder: (@placeholder) ->

  #getTertiary: ->
  #  name: 'Record'
  #  handler: ->
  #    button = $(this)
  #    button.hide()
  #    button.parent().append('<div class="pull-left">Calling +256 778 174 507</div>')

  initialize: (action) ->
    if action.msg
      @setMessage(action.msg)
    if action.uuid
      @setId(action.uuid)
    @recording = action.recording

  getMessage: ->
    return @message

  setMessage: (@message) ->
    if window.base_language
      if not @message
        @message = {}
    @message[window.base_language] = @message

    @markDirty()

  setEmptyMessage: (empty) ->
    @getElement().find('.body .empty').html(empty)

  updateElement: (element) ->
    element.find('.body > .message').text(@message)
    if @recording
      element.find('.body > .play-button').show()
      element.find('audio.player').attr('src', 'http://dl.rapidpro.io/' + @recording).attr('preload', 'auto')
    else
      element.find('.body > .play-button').hide()
    @node.invalidate()

  initializeForm: (form) ->
    super(form)
    textarea = form.find('textarea').text(@getMessage())

    if @placeholder
      textarea.attr('placeholder', @placeholder)

    initAtMessageText(form.find('textarea[name="message"]'), @getFlowVariables())

    form.find("#fileupload").fileupload
      dataType: "json"
      done: (e, data) ->
        $.each data.result.files, (index, file) ->
          $("<p/>").text(file.name).appendTo document.body

  onShow: (form) ->
    form.find('textarea').focus().select()

  validate: (form) ->
    message = form.find('textarea')
    if message.val().strip().length == 0
      message.addClass('error')
      return true
    return false

  submitForm: (form) ->
    @setMessage(form.find('textarea').val())
    $('.node.root').removeClass('initial')

  asJSON: ->
    return { type: 'say', uuid:@getId(), msg: @getMessage(), recording:@recording }

class SetLanguageAction extends Action
  constructor: (@node) -> super('.lang')
  getTitle: -> gettext("Set Language")
  getIcon: -> "icon-language"

  initialize: (action) ->
    @lang = action.lang
    @name = action.name
    @markDirty()

  initializeForm: (form) ->
    super(form)
    lang = form.find('.lang')
    lang.select2().select2('val', @lang)

  submitForm: (form) ->
    data = form.find('.lang').data('select2').data()
    @lang = data.id
    @name = data.text.strip()
    @markDirty()

  updateElement: (ele) ->
    ele.find('.body .language').text(@name)

  asJSON: ->
    return { type: 'lang', lang:@lang, name:@name }

class StartFlowAction extends Action
  constructor: (@node) -> super('.start-flow')
  getTitle: -> gettext("Start Another Flow")
  getIcon: -> "icon-tree"

  initialize: (action) ->
    @flowName = action.name
    @flowId = action.id
    @markDirty()

  initializeForm: (form) ->
    super(form)
    flow = form.find('.new-flow')
    flow.select2().select2('val', @flowId)

  submitForm: (form) ->
    data = form.find('.new-flow').data('select2').data()
    @flowId = data.id
    @flowName = data.text.strip()
    @markDirty()

  updateElement: (ele) ->
    ele.find('.body .flow').text(@flowName)

  asJSON: ->
    return { type: 'flow', id:@flowId, name:@flowName }


class TriggerFlowAction extends Action
  constructor: (@node) ->
    super('.trigger-flow')
    @groups = []
    @contacts = []
    @variables = []

  getTitle: -> gettext("Place Somebody Else in a Flow")
  getIcon: -> "icon-tree"

  setRecipients: (recipients) ->
    @contacts = []
    @groups = []
    @variables = []

    for recipient in recipients
      if recipient.id[0] == 'g'
        @groups.push
          id:parseInt(recipient.id.substring(2)),
          name:recipient.text
      else if recipient.id[0] == 'c'
        @contacts.push
          id:parseInt(recipient.id.substring(2)),
          name:recipient.text
      else if recipient.id[0] == '@'
        @variables.push
          id:recipient.id,
          name:recipient.id
      else if recipient.id[0] == 'n'
        @contacts.push
          phone:recipient.id.substring(2)
          name:recipient.id.substring(2)

    @markDirty()

  initialize: (action) ->
    @flowName = action.name
    @flowId = action.id

    @groups = action.groups
    @contacts = action.contacts

    if action.variables
      @variables = action.variables

    @markDirty()

  initializeForm: (form) ->
    super(form)
    flow = form.find('.new-trigger-flow')
    flow.select2().select2('val', @flowId)

    variables = @getFlowVariables()

    # allow user to create a brand new contact instead
    variables.push({name: 'new_contact', display:"New Contact"});

    # create our omnibox and set the existing data if we have it
    recipients = omnibox(form.find('.recipients'), 'cg', { variables: variables })
    recipients = form.find('.recipients').data('select2')

    data = []
    for group in @groups
      data.push({ id:'g-' + group.id, text:group.name })

    for contact in @contacts
      data.push({ id:'c-' + contact.id, text:contact.name })

    for variable in @variables
      data.push({ id:variable.id, text:variable.id })

    recipients.data(data)

    form.find('.recipients').on 'select2-open', ->
      $(this).removeClass('error')

  validate: (form) ->
    error = false
    recipients = form.find('.recipients')
    if recipients.data('select2').data().length == 0
      recipients.addClass('error')
      error = true

    return error

  submitForm: (form) ->
    data = form.find('.new-trigger-flow').data('select2').data()
    recipients = form.find('.recipients').data('select2')
    @setRecipients(recipients.data())
    @flowId = data.id
    @flowName = data.text.strip()
    @markDirty()

  updateElement: (ele) ->
    ele.find('.body .flow').text(@flowName)

    children = []
    for group in @groups
      groupEle = $("<div class=\"selection omni-option omni-group\"/>")
      groupEle.text(group.name)
      children.push(groupEle)

    for contact in @contacts
      contactEle = $("<div class=\"selection omni-option omni-contact\"/>")
      contactEle.text(contact.name)
      children.push(contactEle)

    for variable in @variables
      variableEle = $("<div class=\"selection\">")
      variableEle.text(variable.id)
      children.push(variableEle)

    ele.find('.recipients').empty().append(children)
    ele.find('.to').show()
    @node.invalidate()

  asJSON: ->
    return { type:'trigger-flow', id:@flowId, name:@flowName, contacts:@contacts, groups:@groups, variables:@variables }


class SaveToContactAction extends Action
  constructor: (@node) -> super('.save-to-contact')
  getTitle: -> gettext("Update Contact")
  getIcon: -> "icon-user"

  initialize: (action) ->
    @label = action.label
    @field = action.field
    @value = action.value
    @markDirty()

  initializeForm: (form) ->
    super(form)
    name = form.find('.field-name')
    value = form.find('.field-value')

    name.select2
      data: contactFields
      query: (query) ->
        data = { results: [] }

        for d in this['data']
          if d.text and query.term
            if d.text.toLowerCase().indexOf(query.term.toLowerCase().strip()) != -1
              data.results.push({ id:d.id, text: d.text });

        if query.term and data.results.length == 0 and query.term.strip().length > 0
          data.results.push({id:'[_NEW_]' + query.term, text: gettext('Add new variable') + ': ' + query.term});

        for c_field in window.extend_contact_fields
          if c_field not in this['data']
            this['data'].push(c_field)

        query.callback(data)

      createSearchChoice: (term, data) ->
        return data

    name.data('select2').val(null)
    if @field
      name.data('select2').data({ id:@field, text:@label })
    else
      if @label
        name.data('select2').data({ id:'[_NEW_]' + @label, text: gettext('Add new variable') + ': ' + @label })

    value.val(@value)

    form.find('.field-name').on 'select2-open', ->
      $(this).removeClass('error')

    initAtMessageText(value, @getFlowVariables())

  validate: (form) ->
    error = false
    if @errorIfEmpty(form.find('.field-name'))
      error = true
    if @errorIfEmpty(form.find('.field-value'))
      error = true
    return error

  submitForm: (form) ->
    field_data = form.find('.field-name').data('select2').data()
    @value = form.find('.field-value').val()

    if field_data.id.indexOf('[_NEW_]') == 0
      @label = field_data.id.substring(7)

      # slugify the label
      slug = @label.toLowerCase().replace(/[^0-9a-z]+/gi, ' ').strip().replace(/[^0-9a-z]+/gi, '_')
      @field = slug

      window.message_completions.push({display:gettext('Contact Field') + ': ' + @label, name:'contact.' + slug})
      window.extend_contact_fields.push({id: @field, text: @label})
    else
      @label = field_data.text
      @field = field_data.id
    @markDirty()

  onShow: (form) ->
    form.find('.field-value').focus().select()

  updateElement: (ele) ->
    ele.find('.body .field').text(@label)
    ele.find('.body .value').text(@value)
    @node.invalidate()

  asJSON: ->
    return { type:'save', field:@field, label:@label, value:@value }


class SendEmailAction extends Action
  constructor: (@node) ->
    super('.send-email')

  getTitle: ->
    return gettext("Send Email")

  initialize: (action) ->
    @setEmail(action.emails, action.subject, action.msg)

  initializeForm: (form) ->
    super(form)

    variables = @getFlowVariables()
    data = []
    for completion in variables
      data.push
        id: '@' + completion.name
        text: '@' + completion.name

    ele = form.find('.email-address').select2
      tags: data
      multiple: true
      selectOnBlur: true
      minimumInputLength: 1

      formatInputTooShort: (term, minLength) ->
        return ""

      matcher: (term, text, opt) ->
       return text.toUpperCase().indexOf(term.toUpperCase()) == 0

      formatNoMatches: (term) ->
        return gettext("Enter a valid e-mail address")

      createSearchChoice: (term, data) ->
        if $(data).filter( -> @text.localeCompare(term) is 0).length is 0
          if /^[^@]+@([^@\.]+\.)+[^@\.]+$/.test(term)
            id: term
            text: term
          else
            null

    ele.data('select2').val(@addresses)
    subject = form.find('.email-subject')
    body =  form.find('.email-body')
    subject.val(@subject)
    body.val(@body)

    initAtMessageText(subject, variables)
    initAtMessageText(body, variables)

    form.find('.email-address').on 'select2-open', ->
      $(this).removeClass('error')


  onShow: (form) ->
    form.find('.email-subject').focus().select()

  setEmail: (@addresses, @subject, @body) ->
    @markDirty()

  validate: (form) ->

    error = false
    if @errorIfEmpty(form.find('.email-address'))
      error = true
    if @errorIfEmpty(form.find('.email-subject'))
      error = true
    if @errorIfEmpty(form.find('.email-body'))
      error = true
    return error

  submitForm: (form) ->
    address = form.find('.email-address').data('select2').val()
    subject = form.find('.email-subject').val()
    body = form.find('.email-body').val()
    @setEmail(address, subject, body)

  updateElement: (ele) ->
    recipients = ""
    for address in @addresses
      recipients += "<div class='selection'>" + address + "</div>"

    ele.find('.to > .recipients').empty().append(recipients)
    ele.find('.body > .message').text(@subject)
    @node.invalidate()

  asJSON: ->
    return { type:'email', emails:@addresses, subject:@subject, msg:@body }


# ----------------------------------------
# Our Rule class, wraps our jquery rule
# ----------------------------------------
class Rule extends Clone

  constructor: (@node, id=uuid()) ->
    super('table tr td.rule', id)

  initializeElement: (ele) ->
    # Without this, Firefox will explictly use 'block' on these tds
    ele.css('display', 'table-cell')
    ele.show()
    @source = ele.find('.source')
    jsPlumb.makeSource(@source, ruleSource)

    source = @source
    rule = @

    @source.on 'click', (evt) ->
      connection = rule.getConnection()
      if connection
        evt.preventDefault()
        evt.stopPropagation()
        hideDragHelper()
        unless canEdit
          return

        modal = new ConfirmationModal(gettext('Remove'), gettext('Are you sure you want to remove this connection?'))
        modal.addClass('alert')
        modal.setListeners
          onPrimary: ->
            jsPlumb.detach(connection)
            markDirty()
        modal.show()
      else
        evt.stopPropagation()
        unless canEdit
          return

        showDragHelper(source.offset(), ruleDragMessage)

  getConnection: ->
    connections = jsPlumb.getConnections({
      source: @source.attr('id'),
      scope: 'actions'
    });

    if connections.length > 0
      return connections[0]

  getSourceId: ->
    return @source.attr('id')

  getNode: ->
    return @node

  createTargetNode: () ->
    actions = new ActionsNode($("#workspace").data("mouse"), uuid())

    if window.is_voice
      action = new SayAction(actions)
    else
      action = new SendResponseAction(actions)

    action['ghost'] = true
    actions.addAction(action)
    return actions

  getOperand: ->
    if typeof(@operand) == "object" and window.base_language
      console.log("getOperand()")
      return @operand[window.base_language]
    return @operand

  getOperandTwo: ->
    return @operandTwo

  getOperator: ->
    return @operator

  getCategory: ->
    if @category
      cat = @category
      if window.base_language
        cat = @category[window.base_language]
      return cat

  setCategory: (category) ->
    if category and window.base_language and not category[window.base_language]
      @category = {}
      @category[window.base_language] = category
    else
      @category = category
    @markDirty()

  updateElement: (ele) ->
    if @useBetween()
      ele.find('.caption').text(@getOperand() + '-' + @getOperandTwo())
    else
      ele.find('.caption').text(@getCategory())

  useBetween: ->
    return @getOperator() in ['between'] and @getCategory() == undefined

  isNumeric: ->
    return @getOperator() == 'between' and @getCategory() == undefined

  update: (values) ->

    if values.uuid
      @setId(values.uuid)

    @operator = values.operator

    is_dict = values.operand and typeof(values.operand) == "object"

    if not is_dict and @operator in ["contains_any", "contains", "regex", "starts"] and window.base_language
      @operand = {}
      @operand[window.base_language] = values.operand
    else
      @operand = values.operand

    if @operator in ["date_equal", "date_after", "date_before"] and values.operand[0] == '@'
      @operand = values.operand.slice(24, -1)

    @operandTwo = values.operandTwo
    @setCategory(values.category)
    @markDirty()

  remove: ->
    if @element
      if @source
        jsPlumb.remove(@source)
      jsPlumb.remove(@element)

  asJSON: ->

    if @getOperator() in ["between"]
      json = {uuid: @getId(), test:{type:@getOperator(), min: @operand, max:@getOperandTwo()}}
    else if @getOperator() in  ["number", "date", "phone", "state"]
      json = {uuid: @getId(), test:{type:@getOperator()}}
    else if @getOperator() in ["date_equal", "date_after", "date_before"]
      json = {uuid: @getId(), test:{type:@getOperator(), test: "@date.today|time_delta:'" + @operand + "'"}}
    else
      json = {uuid: @getId(), test:{type:@getOperator(), test: @operand}}

    if @category
      json['category'] = @category

    connections = jsPlumb.getConnections(
      source: @getSourceId()
      scope: 'actions'
    )

    if connections and connections.length > 0
      json['destination'] = connections[0].targetId
    else
      json['destination'] = null

    console.log(json)
    return json

# ---------------------------------------------
# Handling for a tabbed modal
# ---------------------------------------------
class TabbedModal extends @ConfirmationModal

  constructor: (title, @tabs, @node, removeOnCancel=false) ->
    super(title)

    # Add tab container
    tabContainer = $('#tab-container').clone()
    allTabContent = $('#tab-content').clone()
    modal = @

    for tab in @tabs
      button = @createTabButton(tab)

      # if we only have two tabs, split them evenly
      if @tabs.length == 2
        button.css('width', '50%')

      tabContainer.append(button)
      tabContent = allTabContent.find('#content-' + tab.key)
      @onTabCreate(tab, tabContent)

    @ele.find('.modal-body').append(tabContainer).append(allTabContent)
    @ele.data('object', @node)

    tabContainer.show()

    if @node and @node.hasLabel()
      allTabContent.find('#label-name').val(@node.getLabel())

    # if there are no rules, default to not ignoring
    if @node.getRules().length == 0
      allTabContent.find('#ignore-unknown').attr('checked', false)

    @setListeners
      onBeforePrimary: -> modal.validate()
      onPrimary: -> modal.submit()
      onSecondary: ->
        if removeOnCancel
          node.remove()
          determineDragHelper()

    # our webhook button
    @setTertiaryButton gettext('Webhook'), ->
      node = modal.node
      if modal.validate()
        return
      modal.submit()
      modal.dismiss()
      showWebhookDialog(node)


    # wire up our split operand modal
    if @node.operand and @node.operand.strip() != '@step' and @node.operand.strip() != '@step.value'
      @ele.find('.split-variable').text("the variable " + @node.operand.strip())

    modal = @
    @ele.find('.split-link').on 'click', ->
      if modal.validate()
         return
      modal.submit()
      modal.dismiss()
      showSplitOperandDialog(modal.node)

  shouldRemoveRuleOptions: -> false

  selectTab: (id) ->
    @ele.find('#' + id).click()

  createTabButton: (tab) ->
    modal = @
    button = $('<button/>', { type:'button', class:'btn btn-secondary tab-title'})
    title = $("<div class=title/>").text(tab.title)
    icon = $("<div class='glyph " + tab.icon + "'/>")
    button.append(icon)
    button.append(title)
    button.attr('id', tab.key)

    button.on 'click', ->
      for t in modal.tabs
        if t.key != tab.key
          b = modal.ele.find('#' + t.key)
          if b.hasClass('active')
            modal.onTabHide(t)
            modal.ele.find('#' + t.key).removeClass('active')
            modal.ele.find('#tab-content > #content-' + t.key).hide()
        else
          modal.ele.find('#' + t.key).addClass('active')
          modal.ele.find('#tab-' + t.key).show()
          modal.activeTab = t
          modal.activeTabContent = modal.ele.find('#tab-content > #content-' + t.key)
          modal.activeTabContent.show()
          modal.onTabShow(t, modal.activeTabContent)
          modal.focusFirstInput()

    return button

  validate: ->

    # validate our label name
    labelName = @ele.find('#label-name')
    labelRegex = /^[0-9a-zA-Z_ ]*$/
    labelName.removeClass('error')

    if labelName.val().strip().length == 0
      labelName.addClass('error')
      error = true
    else if not labelName.val().strip().match labelRegex
      labelName.addClass('error')
      labelName.after('<p class="error">' + gettext("Variable name can only contains alphanumeric characters"))
      error = true

    if @onTabValidate(@activeTab, @activeTabContent)
      error = true

    return error

  submit: ->
    @onTabSubmit(@activeTab, @activeTabContent)

    $("#workspace").data("loading", true)
    @node.setLabel(@ele.find('#label-name').val())

    # recalc offsets for ourselves and anybody directly connected to us
    for connected in @node.getConnectedNodes()
      connected.invalidate()

    # wire things up using our old destinations, this is to make sure
    # newly surfaced categories of the same name take over that connection
    @node.wireConnections()

    # now update our destinations according to the visible connections
    # to account for categories that may have been removed
    destinations = {}
    for rule in @node.getRules()
      connection = rule.getConnection()
      if connection and connection.target
        key = getCategory(rule)
        if key
          key = key.toLowerCase()
        destinations[key] = connection.targetId
    @node.setDestinations(destinations)

    # finally, invalidate ourselves so if we changed in size
    # our plumbing lines up properly when we are moved
    @node.invalidate()

    $("#workspace").data("loading", false)
    markDirty()

    # decide if we should pop out the sim button
    updateSimulatorButton(asJSON())

  onTabCreate: (tab, content) ->
  onTabShow: (tab) ->
  onTabHide: (tab) ->
  onTabValidate: (tab, tabContent) ->
  onTabSubmit: (tab, tabContent) ->

  initializeRules: (tabContent, rules=[]) ->

    node = @node

    if rules.length == 0
      rules = @node.getRules()

    # form.attr('id', 'rule-form-body')
    ruleList = tabContent.find('.rule-list')
    ruleList.empty()

    ruleCount = 0
    for rule in rules
      # if operator is true then its the 'everything' case which
      # is always placed at the bottom of the list
      if rule.operator == "true"
        tabContent.find('#ignore-unknown').attr('checked', false)
        category = tabContent.find('.bottom-options .category')
        category.val(getCategory(rule))
        category.data('rule-id', rule.getId())
      else
        row = $("#templates > .rule-row").clone()

        if @shouldRemoveRuleOptions() and (not @node.operand or @node.operand.strip() == '@step.value' or @node.operand.strip() == '@step')
          row.find('option.text').remove()

        if rule.getOperator() in ["between"]
          row.find('.operand').css('display', 'inline-block')
          row.find('#operand-hide').hide()
          row.find("#date-operator-1").hide()
          row.find("#date-operator-2").hide()
          row.find('#operand-two-container').css('display', 'inline-block')
          row.find('.operand').addClass('between')
        else if rule.getOperator() in ["number", "date", "phone", "state"]
          row.find('.operand').hide()
          row.find('#operand-hide').css('display', 'inline-block')
          row.find('#operand-two-container').hide()
          row.find("#date-operator-1").hide()
          row.find("#date-operator-2").hide()
          row.find('.operand').removeClass('between')
        else if rule.getOperator() in ["date_before", "date_equal", "date_after"]
          row.find("#date-operator-1").css('display', 'inline-block')
          row.find("#date-operator-2").css('display', 'inline-block')
          row.find('.operand').css('display', 'inline-block')
          row.find('.operand').addClass('between')
          row.find('#operand-hide').hide()
          row.find('#operand-two-container').hide()
        else
          row.find('.operand').css('display', 'inline-block')
          row.find('#operand-hide').hide()
          row.find("#date-operator-1").hide()
          row.find("#date-operator-2").hide()
          row.find('#operand-two-container').hide()
          row.find('.operand').removeClass('between')


        operator = row.find('select.operator')
        operator.val(rule.getOperator())

        operand = row.find('input.operand')
        operand.val(rule.getOperand())

        category = row.find('input.category')
        category.val(getCategory(rule))
        category.data('auto-gen-disabled', true)

        if rule.getOperator() == "between"
          operandTwo = row.find('input.operand-two')
          operandTwo.val(rule.getOperandTwo())
          if !getCategory(rule)
            category.val(operand.val() + " - " + operandTwo.val())

        row.data('rule-id', rule.getId());
        ruleList.append(row)

        row.data('row', ruleCount++)

    modal = @
    modal.ele.live 'keypress', (evt) ->
      if evt.keyCode == 13
        modal.ele.find('.operand').each ->
          $(this).blur()
        modal.ele.find('.primary').click()

    modal.ele.find(".operand, .category, #label-name").live 'click', (evt) ->
      $(this).removeClass('error')

    modal.ele.find('.operator').live 'change', (evt) ->
      operator = $(this)
      row = operator.parents('.rule-row')

      category = row.find(".category")

      row.find('.operand').attr('placeholder', '')
      row.find('.category').attr('placeholder', '')

      if operator.val() in ["between"]
        row.find('.operand').css('display', 'inline-block')
        row.find('#operand-hide').hide()
        row.find("#date-operator-1").hide()
        row.find("#date-operator-2").hide()
        row.find('#operand-two-container').css('display', 'inline-block')
        row.find('.operand').addClass('between')
      else if operator.val() in ["number", "date", "phone", "state"]
        row.find('.operand').hide()
        row.find('#operand-hide').css('display', 'inline-block')
        row.find('#operand-two-container').hide()
        row.find("#date-operator-1").hide()
        row.find("#date-operator-2").hide()
        row.find('.operand').removeClass('between')
      else if operator.val() in ["date_before", "date_equal", "date_after"]
        row.find("#date-operator-1").css('display', 'inline-block')
        row.find("#date-operator-2").css('display', 'inline-block')
        row.find('.operand').css('display', 'inline-block')
        row.find('.operand').addClass('between')
        row.find('#operand-hide').hide()
        row.find('#operand-two-container').hide()
      else

        if operator.val() == 'district'
          row.find('.operand').attr('placeholder', '@flow.state')
          initAtMessageText(row.find('.operand'), node.getFlowVariables([], {}, true))

        row.find('.operand').css('display', 'inline-block')
        row.find('#operand-hide').hide()
        row.find("#date-operator-1").hide()
        row.find("#date-operator-2").hide()
        row.find('#operand-two-container').hide()
        row.find('.operand').removeClass('between')

      if not category.data('auto-gen-disabled')
        row.find(".operand").blur()

    modal.ele.find(".category").live 'keypress', (evt) ->
      row = $(this).parents('.rule-row')
      category = row.find('.category')

      if not category.data('auto-gen-disabled') and category.val() != category.data('auto-gen-val')
        category.data('auto-gen-disabled', true)

    modal.ele.find('.operand,.operand-two').live 'blur', (evt) ->
      row = $(this).parents('.rule-row')
      category = row.find('.category')
      operator = row.find('.operator')
      operand = row.find('.operand')
      operandTwo = row.find('.operand-two')

      # see if we need to auto-generate our default cateogry
      needsDefaultCategory = false
      if category.val().strip().length == 0 or category.val() in ["numeric", "is a date", "district", "state"]
        needsDefaultCategory = operand.val().strip().length > 0
        if operator.val() == "between" and needsDefaultCategory and operandTwo.val().strip().length == 0
          needsDefaultCategory = false

      # if we need a default, populate it
      if needsDefaultCategory or not category.data('auto-gen-disabled')
        categoryName = operand.val()
        if operator.val() in ["between"]
          categoryName = operand.val() + " - " + operandTwo.val()
        else if operator.val() == "number"
          categoryName = "numeric"
        else if operator.val() == "district"
          categoryName = "district"
        else if operator.val() == "state"
          categoryName = "state"
        else if operator.val() == "phone"
          categoryName = "phone"
        else if operator.val() == "regex"
          categoryName = "matches"
        else if operator.val() == "date"
          categoryName = "is a date"
        else if operator.val() in ["date_before", "date_equal", "date_after"]
          if operand.val()[0] == '-'
            categoryName = "today " + operand.val()
          else
            categoryName = "today +" + operand.val()

          if operand.val() in ['1', '-1']
            categoryName = categoryName + " day"
          else
            categoryName = categoryName + " days"

          if operator.val() == 'date_before'
            categoryName = "< " + categoryName
          else if operator.val() == 'date_equal'
            categoryName = "= " + categoryName
          else if operator.val() == 'date_after'
            categoryName = "> " + categoryName

        # this is a rule matching keywords
        else if operator.val() in ["contains", "contains_any", "starts"]
          # take only the first word and title case it.. so "yes y ya" turns into "Yes"
          words = categoryName.trim().split(/\b/)
          if words
            categoryName = words[0].toUpperCase()
            if categoryName.length > 1
              categoryName = categoryName.charAt(0) + categoryName.substr(1).toLowerCase()

        else
          op = operator.val()
          named = opNames[op]
          if named
            categoryName = named + categoryName

        # limit category names to 36 chars
        categoryName = categoryName.substr(0, 36)

        category.val(categoryName)
        category.data('auto-gen-disabled', false)
        category.data('auto-gen-val', categoryName)
        modal.ensureEmptyRule(tabContent)

    modal.ele.find(".operand").live 'keyup', (evt) ->
      modal.ensureEmptyRule(tabContent)

    modal.ele.find('.close').live 'click', (evt) ->
      evt.stopPropagation()
      $(this).parents('.rule-row').remove()
      modal.ensureEmptyRule(tabContent)
    modal.ensureEmptyRule(tabContent)

  ensureEmptyRule: (ruleTab) ->

    ruleList = ruleTab.find('.rule-list')
    rules = ruleList.children('.rule-row')
    hasEmpty = false

    for row in rules
      if not hasEmpty
        op = $(row).find('.operand')
        if op.is(":visible")
          hasEmpty = op.val().strip().length == 0

    if not hasEmpty
      row = $("#templates > .rule-row").clone()
      row.find('#operand-two-container').hide()

      if @shouldRemoveRuleOptions() and (not @node.operand or @node.operand.strip() == '@step' or @node.operand.strip() == '@step.value')
        row.find('option.text').remove()

      if rules.length == 0
        row.find('.operand').attr('placeholder', 'yes y si')
        row.find('.category').attr('placeholder', 'Yes')

      ruleList.append(row)

    options = @ele.find('#category-options')
    rules = ruleList.children('.rule-row')
    if rules.length > 1
      if not options.is(':visible')
        options.show()

      ruleList.sortable
        placeholder: "sort-placeholder"
        forcePlaceholderSize: true
        scroll:false

    else
      if options.is(':visible')
        options.find('#ignore-unknown').attr('checked', false)
        options.hide()

  validateRules: (tabContent) ->
    ruleList = tabContent.find('.rule-list')
    if ruleList.is(":visible")
      for row in ruleList.children('.rule-row')
        row = $(row);
        ruleId = row.data('rule-id')
        row.find('.error-message').hide()

        operand = row.find('.operand')
        operandTwo = row.find('.operand-two')
        category = row.find('.category')
        operator = row.find('.operator')

        operand.removeClass('error')
        operandTwo.removeClass('error')
        category.removeClass('error')

        hasOperand = operand.val().strip().length > 0
        hasOperandTwo = operandTwo.val().strip().length > 0
        hasCategory = category.val().strip().length > 0


        if hasCategory
          emptyRules = false

        if ruleId and (operator.val() in ["number", "phone", "regex", "state"])
          if not hasCategory
            category.addClass('error')
            error = true

        if ruleId and operator.val() not in ["number", "date", "phone", "state"]
          if hasOperand or hasCategory
            if not hasOperand
              operand.addClass('error')
              error = true

            if not hasCategory
              category.addClass('error')
              error = true
          else
            row.remove()

        if hasOperand and not hasCategory and operator.val() not in ["number", "date", "phone", "state"]
          category.addClass('error')
          error = true

        if not hasOperand and hasCategory and operator.val() not in ["number", "date", "phone", "state"]
          operand.addClass('error')
          error = true

        if operator.val() in ["between"]
          if hasOperandTwo and not hasCategory
            error = true
            category.addClass('error')

          if hasCategory and not hasOperandTwo
            error = true
            operandTwo.addClass('error')

        # make sure we have numbers in the operands for number operators and date operators
        if operator.val() in ["between", "gt", "eq", "lt", "date_before", "date_equal", "date_after"]
          if operand.val().indexOf('@') < 0 and isNaN(+operand.val())
            error = true
            operand.addClass('error')
            row.find('.error-message').text("'" + operand.val() + "' " + gettext("is not a number")).show()

          if operator.val() == "between"
            if operandTwo.val().indexOf('@') < 0 and isNaN(+operandTwo.val())
              error = true
              operandTwo.addClass('error')
              row.find('.error-message').text("'" + operandTwo.val() + "' " + gettext("is not a number")).show()

            # if no error yet, check range is in proper order
            if not row.find('.error-message').is(':visible')
              if parseFloat(operand.val()) >= parseFloat(operandTwo.val())
                error = true
                row.find('.error-message').text(gettext("The second number") + " (" + operandTwo.val() + ") " + gettext("should be larger than the first one")).show()
    return error

  getRulesFromList: (tabContent) ->
    ruleList = tabContent.find('.rule-list')
    rules = []
    for row in ruleList.children('.rule-row')
      row = $(row)
      id = row.data('rule-id')
      category = row.find('.category').val()
      if not id and not category
        continue
      rules.push
        id: id
        category: category
        operator: row.find('.operator').val()
        operand: row.find('.operand').val()
        operandTwo: row.find('.operand-two').val()
    return rules

  addEverythingRule: (rules) ->
    # lookup our everything else category
    category = 'All Responses'
    if rules.length > 0
      category = 'Other'

    if window.base_language
      cat = {}
      cat[window.base_language] = category
      category = cat

    rules.push
      id: @node.getEverythingId()
      operator: "true"
      operand: "true"
      category: category

    return rules


# ---------------------------------------------
# Modal to handle our keypad-based dialog
# ---------------------------------------------
class KeypadResponseModal extends TabbedModal

  tabs = [
    { key: 'menu', title: 'Menu', icon: 'icon-list', },
    { key: 'keypad-series', title: 'Keypad', icon: 'icon-grid', }
    # { key: 'recording', title: 'Recording', icon: 'icon-phone', }
  ]

  constructor: (@node, removeOnCancel=false) ->
    super(gettext('Save Response'), tabs, @node, removeOnCancel)
    @addClass('rules-dialog')
    @keyboard = false

    if @node.finishedKey
      @selectTab('keypad-series')
    else
      @selectTab('menu')

  shouldRemoveRuleOptions: -> true

  onTabCreate: (tab, tabContent) ->
    if tab.key == 'keypad-series'
      @initializeRules(tabContent)
    if tab.key == 'menu'
      rules = @node.getRules()
      for rule in rules
        if rule.operator == 'eq'
          tabContent.find('.num-' + rule.operand + ' input').val(getCategory(rule))
          tabContent.find('.num-' + rule.operand).attr('id', rule.id)

  onTabValidate: (tab, tabContent) ->
    if tab.key == 'keypad-series'
      @validateRules(tabContent)

  onTabSubmit: (tab, tabContent) ->
    rules = []
    if tab.key == 'keypad-series'
      rules = @getRulesFromList(tabContent)
      @node.setFinishedKey('#')
    else if tab.key == 'menu'
      for i in '1234567890'
        id = tabContent.find('.num-' + i).attr('id')
        category = tabContent.find('.num-' + i + " input").val().strip()

        # generate our rule id if we don't have one
        if not id
          id = uuid()

        if category.length > 0
          # console.log("Adding '" + category + "': " + id)
          rules.push
            id: id
            category: category
            operator: 'eq'
            operand: i
      # menus don't have a finish key or custom operand
      @node.setFinishedKey(null)
      @node.setOperand(null)

    # append our everything rule on the end
    @addEverythingRule(rules)
    @node.setRules(rules)

# ---------------------------------------------
# Modal to handle our keypad-based dialog
# ---------------------------------------------
class SMSResponseModal extends TabbedModal

  tabs = [
    { key: 'open', title: 'Open Ended', icon: 'icon-bubble-dots-2', },
    { key: 'multiple', title: 'Multiple Choice', icon: 'icon-stack', },
    { key: 'numeric', title: 'Numeric', icon: 'icon-numerical', }
  ]

  constructor: (@node, removeOnCancel=false) ->
    super(gettext('Save Response'), tabs, @node, removeOnCancel)
    @addClass('rules-dialog')
    @keyboard = false

    rules = @node.getRules()
    # figure out which tab to open first
    if rules.length > 1
      if rules.length == 2 and rules[0].operator == "between"
        @selectTab('numeric')
      else
        @selectTab('multiple')
    else
      @selectTab('open')

  onTabCreate: (tab, tabContent) ->
    if tab.key == 'multiple'
      @initializeRules(tabContent)
    if tab.key == 'numeric'
      # keep track of our numeric rule so it doesn't get a new id
      rules = @node.getRules()
      if rules.length == 2
        tabContent.data('object', rules[0])

  onTabShow: (tab, tabContent) ->
    if tab.key == 'numeric'
      rules = @getRulesFromList(@ele.find("#content-multiple"))
      if rules.length == 1 and rules[0].operator == "between"
        tabContent.find('#numeric-min').val(rules[0].operand)
        tabContent.find('#numeric-max').val(rules[0].operandTwo)
    if tab.key == 'multiple'
      rules = @getRulesFromList(@ele.find("#content-multiple"))
      # see if we should update our first rule based on the numeric tab
      if rules.length == 0
        numericTabContent = @ele.find("#content-numeric")
        one = numericTabContent.find('#numeric-min').val()
        two = numericTabContent.find('#numeric-max').val()
        if one.length > 0 and two.length > 0 and isNumber(one) and isNumber(two)
          # create a betwen rule from our numeric tab
          rule =
            getOperator: -> 'between'
            getOperand: -> one
            getOperandTwo: -> two
            getCategory: -> one + ' - ' + two
            getId: ->

          @initializeRules(tabContent, [rule])

  onTabValidate: (tab, tabContent) ->

    if tab.key == 'multiple'
      return @validateRules(tabContent)

    else if tab.key == 'numeric'
      return @validateNumeric(tabContent)

  onTabSubmit: (tab, tabContent) ->

    rules = []
    if tab.key == 'numeric'
      rules = @getNumericRules(tabContent)
    else if tab.key == 'multiple' or tab.key == 'open'
      rules = @getRulesFromList(tabContent)

    # append our everything rule on the end
    @addEverythingRule(rules)
    @node.setRules(rules)

  getNumericRules: (tabContent) ->
    rules = []
    rule = tabContent.data('object')
    id = undefined
    if rule
      id = rule.getId()

    min = tabContent.find('#numeric-min').val()
    max = tabContent.find('#numeric-max').val()
    rules.push
      id: id
      operator: 'between'
      operand: min
      operandTwo: max
    return rules

  validateNumeric: (tabContent) ->
    error = false
    min = tabContent.find('#numeric-min')
    max = tabContent.find('#numeric-max')
    min.removeClass('error')
    max.removeClass('error')

    if min.val().strip().length == 0
      error = true
      min.addClass('error')

    if max.val().strip().length == 0
      error = true
      max.addClass('error')

    if isNaN(+min.val())
      error = true
      min.addClass('error')
      tabContent.find('.error-message').text("'" + min.val() + "' " + gettext("is not a valid number")).show()
    if isNaN(+max.val())
      error = true
      max.addClass('error')
      tabContent.find('.error-message').text("'" + max.val() + "' " + gettext("is not a valid number")).show()

    if parseFloat(min.val()) > parseFloat(max.val())
      error = true
      min.addClass('error')
      max.addClass('error')
      tabContent.find('.error-message').text(gettext("The second number") + " (" + max.val() + ") " + gettext("should be larger than the first one")).show()

    return error


# ---------------------------------------------
# Things to wire after our DOM is ready to roll
# ---------------------------------------------
$ ->
  # $(".operand").live "blur", (evt) ->
  #   getNode($(this)).ensureEmptyRule()

  $('.source-disabled').live 'mousedown', (evt) ->
    hideDragHelper()
    modal = new ConfirmationModal(gettext('End of Branch'), gettext('You must first add a response to this branch in order to extend it.'))
    modal.setPrimaryButton(gettext('Add Response'))

    node = $(this).parent('.node').data('object')

    modal.setListeners
      onPrimary: ->
        modal.dismiss()

        sends = node.getElement().find('.actions > .action.send-response, .actions > .action.say')
        if sends.length > 0
          showActionDialog($(sends[0]).data('object'))
        else
          if window.is_voice
            showActionDialog(new SayAction(node))
          else
            showActionDialog(new SendResponseAction(node))

    modal.show()

  $('.webhook .close').live 'click', (evt) ->
    evt.stopPropagation()
    hideDragHelper()
    unless canEdit
      return

    del = $(this).parents('.webhook').find('.delete')
    node = $(this).parents('.node').data('object')

    if del.is(':visible')
      node.setWebhook(null)
      node.invalidate()
      markDirty()
    else
      del.fadeIn()
      window.setTimeout (-> del.fadeOut()), 3000

  $('.ruleset .close').live 'click', (evt) ->
    evt.stopPropagation()
    hideDragHelper()
    unless canEdit
      return

    node = $(this).parents('.node').data('object')

    del = $(this).parents('td').find('.delete')
    node = $(this).parents('.node').data('object')

    if del.is(':visible')
      node.remove()
      if $("#workspace .node").length == 0
        createRootNode()
      markDirty()
    else
      del.fadeIn()
      window.setTimeout (-> del.fadeOut()), 3000

  $('.action .move-up').live 'click', (evt) ->
    evt.stopPropagation()
    unless canEdit
      return

    action = $(this).parents('.action').data('object')
    action.moveup()


  $('.action .close').live 'click', (evt) ->
    evt.stopPropagation()
    hideDragHelper()
    unless canEdit
      return

    ele = $(this).parents('.action')
    action = ele.data('object')

    if ele.find('.delete').is(':visible')
      action.remove()
      if action.node.hasActions()
        action.node.invalidate()
      else
        action.node.remove()
        if $("#workspace .node").length == 0
          createRootNode()
      markDirty()
    else
      ele.find('.delete').fadeIn()
      window.setTimeout (-> ele.find('.delete').fadeOut()), 3000

  $(".node .webhook").live "click", ->
    hideDragHelper()
    unless canEdit
      return

    node = $(this).parents('.node').data('object')
    showWebhookDialog(node)


  $(".node .ruleset").live "click", ->
    hideDragHelper()
    unless canEdit
      return

    node = $(this).parents('.node').data('object')
    # modal = new SaveResponseModal(node)
    # modal = new KeypadResponseModal(node)
    modal = new getResponseModal(node)
    modal.show()
    if node.getRules().length == 1
      modal.ele.find('#label-name').select().focus()

  $(".action").live "click", ->
    hideDragHelper()
    unless canEdit
      return

    action = $(this).data('object')

    # create call
    if active_call
      $('.recording').each -> $(this).removeClass('recording')
      action.getElement().addClass('recording')
      url = '/recording/' + flowId + '/' + action.node.getId() + '/' + action.getId() + '/'

      if call_id
        url += '?call=' + call_id

      # initiate the call
      $.ajax({
        type: "GET",
        url: url,
      }).done((data) ->
        if active_call
          call_id = data['call_id']
      )

    else
      showActionDialog(action)

  $('.ruleset, .action, .webhook').live('mouseenter', onItemHover).live('mouseleave', onItemUnhover)
  $('.node.actions').live('mouseenter', onNodeHover).live('mouseleave', onNodeUnhover)

  $('.add-action').live 'click', ->
    unless canEdit
      return
    node = $(this).parent('.node').data('object')

    if window.is_voice
      showActionDialog(new SayAction(node))
    else
      showActionDialog(new SendResponseAction(node))

  $('.activity').live('click', onActivityClick)

  # wire up our plumbing
  jsPlumb.ready ->
    jsPlumb.bind('connectionDragStop', onDragStop)
    jsPlumb.bind('connectionDrag', onDragStart)

    console.log(initial)
    if initial
      initialize(initial)

    if $("#workspace .node").length == 0
      createRootNode()

# ---------------------------------------------
# Event handlers
# ---------------------------------------------
onDragStart = (connection) ->
  unless canEdit
    return

  $("#workspace").data('dragging', true)

  hideDragHelper()

  rule = getRule(connection.source)
  if !rule
    node = getNode(connection.source)
    ghost = node.createTargetNode()
  else
    ghost = rule.createTargetNode()

  ghost.appendToWorkspace()
  ghost.setGhost(true)

  $("#workspace").data('ghost', ghost).data('source', connection.source).data('activeConnection', connection)
  return true

onDragStop = (connection) ->
  unless canEdit
    return

  ghost = $("#workspace").data('ghost')
  if ghost
    # ghost.checkForCollisions()
    ele = ghost.getElement()

    if not ele.hasClass('collides') and ele.is(":visible")
      ghost.setGhost(false)
      ghost.enableDrop()
      ghost.enableDrag()

      node = getNode("#" + connection.sourceId)
      connect(connection.sourceId, ghost.getId(), node.getScope())
      ghost.onCreate()

  $('#workspace').find('.ghost').remove()

  $('#workspace').remove('.ghost').data('ghost', null).data('dragging', false)
  markDirty()

  # update our connection
  source = getNode(connection.source)

  if source
    source.setDestination(connection.targetId)

  determineDragHelper()
  return true

onMessageSubmit = (modal) ->
  form = modal.ele.find('#message-form-body')
  action = form.data('object')
  action.setMessage(form.find('textarea').val())
  markDirty()

createRootNode = ->
  root = new ActionsNode({left:100, top:0})

  if window.is_voice
    action = new SayAction(root)
    action.setEmptyMessage('<p>' + gettext("Enter the message to read out to the contact when they answer the phone.") + '</p>')
    action.setPlaceHolder(gettext('Hi @contact.name, do you have a moment to participate in a short poll?'))
  else
    action = new SendResponseAction(root)
    action.setEmptyMessage("<p>" + gettext("To get your flow started, you need to send a message.") + "</p><p>" + gettext("Click here to create the first message in your flow.") + "</p>")
    action.setPlaceHolder(gettext("Hi @contact.name! Will you be able to attend the training on Saturday?"))

  action.msg = {}

  root.addAction(action)
  root.appendToWorkspace()
  root.setRoot(true)
  root.setInitial(true)
  if canEdit
    root.enableDrop()
    root.enableDrag()

$ ->
  $("#workspace").on('mousemove', (evt) ->
    $(this).data('mouse', { left: evt.pageX, top: evt.pageY})
    ghost = $(this).data('ghost')
    if ghost
      ghost.moveTo({ left: evt.pageX - wsOffset.left - (ghost.getElement().outerWidth() / 2), top: evt.pageY - wsOffset.top })
      if $("#workspace > .drop-hover").length > 0
        ghost.getElement().hide()
      else
        ghost.getElement().show()
  )


onRuleHover = ->
  ele = $(this)
  ruleNode = ele.parents('.node').data('object')
  rules = ruleNode.getRules()
  #if rules.length == 1
  #  rules[0].element.find('.add-rules').show()
  #  rules[0].element.find('.caption').css('visibility', 'hidden')

onRuleUnhover = ->
  ele = $(this)
  ruleNode = ele.parents('.node').data('object')
  #rules = ruleNode.getRules()
  #if rules.length == 1
  #  rules[0].element.find('.add-rules').hide()
  #  rules[0].element.find('.caption').css('visibility', 'visible')



onItemHover = ->
  ele = $(this)
  unless canEdit
    return

  unless active_call
    ele.find('.close').show()

    # if there's a previous action, show up arrow
    if ele.prev().hasClass('action')
      ele.find('.move-up').show()

onItemUnhover = ->
  ele = $(this)
  ele.find('.close, .move-up').hide()

onNodeHover = ->
  unless canEdit
    return
  unless active_call
    $(this).find('.add-action').show()

onNodeUnhover = ->
  $(this).find('.add-action').hide()

onActivityClick = (evt) ->
  evt.stopPropagation()
  unless canEdit
    return

  node = getNode($(this))

  if initial['archived']
    showWarning(gettext("Archived Survey"), gettext("This Survey is Archived.") + "<br/>" + gettext("You cannot broadcast to an archived survey"))
  else
    broadcastToNode(node.getId())

checkDirty = ->
  if dirty
    save()

# Export our structure as JSON
asJSON = ->
  actions = []
  rules = []
  notes = []
  entry = null

  for node in $("#workspace").children('.node')
    node = $(node).data('object')

    if node.isGhost()
      continue

    if node instanceof RulesNode
      rules[rules.length] = node.asJSON()
    else if node instanceof ActionsNode
      if node.getElement().hasClass('root')
        entry = node.getId()
      actions[actions.length] = node.asJSON()

  for note in $("#workspace").children('.sticky')
    note = $(note).data('object')
    if note.title or note.body
      notes[notes.length] = note.asJSON()

  json = { rule_sets: rules, action_sets: actions, metadata: { notes: notes }, last_saved: lastSaved }

  # set the base language
  if window.base_language
    json['base_language'] = window.base_language

  if entry
    json['entry'] = entry

  return json

# ---------------------------------------------
# On load events
# ---------------------------------------------
$ ->
  setInterval(checkDirty, 1000)
  updateActivity()

  $('.play-button').on 'click', (e) ->
    e.preventDefault()
    e.stopPropagation()
    unless canEdit
     return

    action = $(this).parents('.action').data('object')
    if action.recording
      $('audio.player').each ->
        audio = $(this)[0]
        if not audio.paused
          audio.pause()
          audio.currentTime = 0
      $(this).parent().find('audio.player')[0].play()

  $('.record-prompts').click ->
    unless canEdit
      return

    modal = new Modax(gettext("Record Prompts"), '/usersettings/phone/')
    modal.setIcon("icon-phone")
    modal.setListeners
      onSuccess: ->
        setCallActive(true)
    modal.show()

  $('.test-call').click ->
    unless canEdit
      return

    modal = new Modax(gettext("Test Call"), '/usersettings/phone/')
    modal.setIcon("icon-phone")
    modal.setListeners
      onSuccess: ->
        $.post(simulateURL, JSON.stringify({ has_refresh:true })).done (data) ->
          window.simulation = true
          #updateSimulator(data)
    modal.show()

  $('.hangup').click ->
    unless canEdit
      return
    setCallActive(false)

  # Clicking on workspace creates notes
  $("#workspace").dblclick (e) ->
    unless canEdit
      return
    if not active_call
      note = new Note()
      note.initialize
        title: 'Note Title'
        body: '...'
        x: e.pageX - 20
        y: e.pageY - wsOffset.top - 20

  $(window).resize ->
    $("#header, #footer").css("width", $(document).width())

  $(window).scroll ->
    width = $(document).width();
    $("#header").css("width", width)
    $("#footer").css("width", width)
