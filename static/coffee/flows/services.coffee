app = angular.module('temba.services', [])

version = new Date().getTime()

quietPeriod = 500
errorRetries = 10

app.service "utils", ['$modal', ($modal) ->

  isWindow = (obj) ->
    obj and obj.document and obj.location and obj.alert and obj.setInterval

  isScope = (obj) ->
    obj and obj.$evalAsync and obj.$watch

  # our json replacer strips out variables with leading underscores
  toJsonReplacer = (key, value) ->
    val = value
    if typeof key is "string" and (key.charAt(0) is "$" or key.charAt(0) is "_")
      val = undefined
    else if isWindow(value)
      val = "$WINDOW"
    else if value and document is value
      val = "$DOCUMENT"
    else if isScope(value)
      val = "$SCOPE"

    return val

  toJson: (obj, pretty) ->
    if typeof obj == 'undefined'
      return undefined
    return JSON.stringify(obj, toJsonReplacer, pretty ? '  ' : null);

  clone: (obj) ->
    if not obj? or typeof obj isnt 'object'
      return obj

    if obj instanceof Date
      return new Date(obj.getTime())

    if obj instanceof RegExp
      flags = ''
      flags += 'g' if obj.global?
      flags += 'i' if obj.ignoreCase?
      flags += 'm' if obj.multiline?
      flags += 'y' if obj.sticky?
      return new RegExp(obj.source, flags)

    newInstance = new obj.constructor()

    for key of obj
      newInstance[key] = this.clone obj[key]

    return newInstance

  checkCollisions: (ele) ->
    nodes = ele.parent().children('.node')
    collision = false
    for node in nodes
      if node != ele[0]
        if this.collides($(node), ele)
          collision = true
          break

    if collision
      ele.addClass("collision")
    else
      ele.removeClass("collision")

  # does one element collide with another element
  collides: (a, b) ->
    aOffset = a.offset()
    bOffset = b.offset()

    aBox =
      left: aOffset.left
      top: aOffset.top
      bottom: a.outerHeight() + aOffset.top
      right: a.outerWidth() + aOffset.left

    bBox =
      left: bOffset.left
      top: bOffset.top
      bottom: b.outerHeight() + bOffset.top
      right: b.outerWidth() + bOffset.left

    if aBox.bottom < bBox.top
      return false
    if aBox.top > bBox.bottom
      return false
    if aBox.left > bBox.right
      return false
    if aBox.right < bBox.left
      return false
    return true

  zip: () ->
    lengthArray = (arr.length for arr in arguments)
    length = Math.min(lengthArray...)
    for i in [0...length]
      arr[i] for arr in arguments

  openModal: (templateUrl, controller, resolveObj) ->
    $modal.open
      keyboard: false
      templateUrl: templateUrl
      controller: controller
      resolve: resolveObj

]
#============================================================================
# DragHelper is all kinds of bad. This facilitates the little helper cues
# for the user so they learn the mechanics of building a flow. We should
# find a more angular way to do this, but at present there's all kinds of
# DOM inspection and manipulation when using this guy.
#============================================================================
app.service 'DragHelper', ['$rootScope', '$timeout', '$log', ($rootScope, $timeout, $log) ->

  show: (source, message) ->
    sourceOffset = source.offset()

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
        if $rootScope.dragHelperId
          $timeout.cancel($rootScope.dragHelperId)
          $rootScope.dragHelperId = undefined
        $rootScope.dragHelperId = $timeout ->
          helper.fadeOut()
        ,20000

  showSaveResponse: (source) ->
    @show(source, 'To save responses to this message <span class="attn">drag</span> the red box')

  showSendReply: (source) ->
    @show(source, 'To send back a reply <span class="attn">drag</span> the red box')

  hide: ->
    $('#drag-helper').fadeOut()
    if $rootScope.dragHelperId
      $timeout.cancel($rootScope.dragHelperId)
      $rootScope.dragHelperId = undefined

]

#============================================================================
# Plumb service for mananging all the JSPlumb chicanery
#============================================================================
app.service "Plumb", ["$timeout", "$rootScope", "$log", ($timeout, $rootScope, $log) ->

  # Don't worry about drawing until after we've done our initial load
  jsPlumb.setSuspendDrawing(true)
  $('#flow').css('visibility', 'hidden')
  $timeout ->
    $('#flow').css('visibility', 'visible')
    jsPlumb.setSuspendDrawing(false)
    jsPlumb.repaintEverything()
  ,500

  jsPlumb.importDefaults
    DragOptions : { cursor: 'pointer', zIndex:2000 }
    DropOptions : { tolerance:"touch", hoverClass:"drop-hover" }
    Endpoint: "Blank"
    EndpointStyle: { strokeStyle: "transparent" }
    PaintStyle: { lineWidth:5, strokeStyle:"#98C0D9" }
    HoverPaintStyle: { strokeStyle: "#27ae60"}
    HoverClass: "connector-hover"
    ConnectionsDetachable: window.mutable
    Connector:
      [ "Flowchart",
          stub: 12
          midpoint: .85
          alwaysRespectStubs: false
          gap:[0,7]
          cornerRadius: 2
      ]

    ConnectionOverlays : [
      ["PlainArrow", { location:.9999, width: 12, length:12, foldback: 1 }],
    ]

    Container: "flow"

  targetDefaults =
    anchor: [ "Continuous", { faces:["top", "left", "right"] }]
    endpoint: [ "Rectangle", { width: 20, height: 20, hoverClass: 'endpoint-hover' }]
    hoverClass: 'target-hover'
    dropOptions: { tolerance:"touch", hoverClass:"drop-hover" }
    dragAllowedWhenFull: false
    deleteEndpointsOnDetach: true
    isTarget:true

  sourceDefaults =
    anchor: "BottomCenter"
    deleteEndpointsOnDetach: true
    maxConnections:1
    dragAllowedWhenFull:false
    isSource:true
    paintStyle:{ fillStyle:"blue", outlineColor:"black", outlineWidth:1 }

  makeSource: (sourceId, scope) ->
    # we do this in the next cycle to make sure our id is set
    $timeout ->
      jsPlumb.makeSource sourceId, angular.extend({scope:scope}, sourceDefaults)
    ,0

  makeTarget: (targetId, scope) ->
    $timeout ->
      jsPlumb.makeTarget targetId, angular.extend({scope:scope}, targetDefaults)
    ,0

  getSourceConnection: (source) ->
    connections = jsPlumb.getConnections({
      source: source.attr('id'),
      scope: '*'
    });

    if connections and connections.length > 0
      return connections[0]

  detachSingleConnection: (connection) ->
    jsPlumb.detach(connection)

  recalculateOffsets: (nodeId) ->

    # don't bother with offsets if we are running inside a test
    if window.testing
      return

    # update ourselves
    $timeout ->

      # this reassesses our offsets
      jsPlumb.revalidate(nodeId)

      # this updates the offsets for our child elements
      jsPlumb.recalculateOffsets(nodeId)

      # finally repaint our new hotness
      jsPlumb.repaint(nodeId)
    ,0

  removeElement: (id) ->
    jsPlumb.remove(id)

  disconnectAllConnections: (id) ->

    # reenable any sources connecting to us
    jsPlumb.select({target:id}).each (connection) ->
      jsPlumb.setSourceEnabled(connection.sourceId, true)

    # now disconnect the existing connections
    jsPlumb.detachAllConnections(id)

    $('#' + id + ' .source').each ->
      id = $(this).attr('id')
      jsPlumb.detachAllConnections(id)

  disconnectOutboundConnections: (id) ->
    jsPlumb.detachAllConnections(id)
    if jsPlumb.isSource(id)
      jsPlumb.setSourceEnabled(id, true)

  setSourceEnabled: (source, enabled) ->
    jsPlumb.setSourceEnabled(source, enabled)


  connect: (sourceId, targetId, scope, fireEvent = true) ->

    #$log.debug(sourceId + ' > ' + targetId)

    sourceId += '_source'

    # remove any existing connections for our source first
    Plumb = @
    Plumb.disconnectOutboundConnections(sourceId)

    # connect to our new target if we have one
    if targetId?
      existing = jsPlumb.getEndpoints(targetId)
      targetPoint = null
      if existing
        for endpoint in existing
          if endpoint.connections.length == 0
            targetPoint = existing[0]
            break

      if not targetPoint
        targetPoint = jsPlumb.addEndpoint(targetId, { scope: scope }, targetDefaults)

      if jsPlumb.getConnections({source:sourceId, scope:scope}).length == 0

        if jsPlumb.isSource(sourceId)
          Plumb.setSourceEnabled(sourceId, true)

        jsPlumb.connect
          maxConnections:1
          dragAllowedWhenFull:false
          deleteEndpointsOnDetach:true
          editable:false
          source: sourceId
          target: targetPoint
          fireEvent: fireEvent

        $timeout ->
          Plumb.setSourceEnabled(sourceId, false)
          Plumb.repaint(sourceId)
        ,0

  # Update the connections according to the destination. Peforms update
  # after $digest to make sure DOM element is ready for jsPlumb.
  updateConnection: (actionset) ->
    Plumb = @
    $timeout ->
      Plumb.disconnectOutboundConnections(actionset.uuid + '_source')
      if actionset.destination
        Plumb.connect(actionset.uuid, actionset.destination, 'rules')
      Plumb.recalculateOffsets(actionset.uuid)
    ,0

  # Update the connections according to the category targets. Performs update
  # after $digest to make sure DOM elements are ready for jsPlumb.
  updateConnections: (ruleset) ->
    Plumb = @
    $timeout ->
      for category in ruleset._categories
        Plumb.connect(ruleset.uuid + '_' + category.source, category.target, 'actions')
      Plumb.recalculateOffsets(ruleset.uuid)
    ,0

  setPageHeight: ->
    $("#flow").each ->
      pageHeight = 0
      $this = $(this)
      $.each $this.children(), ->
        child = $(this)
        bottom = child.offset().top + child.height()
        if bottom > pageHeight
          pageHeight = bottom + 500
      $this.height(pageHeight)

  repaint: (element=null) ->
    if not window.loaded
      return

    service = @

    $timeout ->

      if element
        jsPlumb.repaint(element)
      else
        jsPlumb.repaintEverything()
    , 0

    $timeout ->
      service.setPageHeight()
    , 0


  disconnectRules: (rules) ->
    for rule in rules
      jsPlumb.remove(rule.uuid + '_source')

  getConnectionMap: (selector = {}) ->

    # get the current connections as a map
    connections = {}
    jsPlumb.select(selector).each (connection) ->
      # only count legitimate targets
      if connection.targetId and connection.targetId.length > 24
        # strip off _source suffix
        source = connection.sourceId.substr(0, connection.sourceId.lastIndexOf('_'))
        connections[source] = connection.targetId

    return connections
]

app.factory "Revisions", ['$http', '$log', ($http, $log) ->
  new class Revisions
    updateRevisions: (flowId) ->
      _this = @
      $http.get('/flow/revisions/' + flowId + '/').success (data, status, headers) ->
        # only set the revisions if we get back json, if we don't have permission we'll get a login page
        if headers('content-type') == 'application/json'
          _this.revisions = data

    getRevision: (revision) ->
      _this = @
      return $http.get('/flow/revisions/' + flowId + '/?definition=' + revision.id).success (data, status, headers) ->
        # only set the revisions if we get back json, if we don't have permission we'll get a login page
        if headers('content-type') == 'application/json'
          _this.definition = data
]

app.factory 'Flow', ['$rootScope', '$window', '$http', '$timeout', '$interval', '$log', 'utils', 'Plumb', 'Revisions', 'DragHelper', ($rootScope, $window, $http, $timeout, $interval, $log, utils, Plumb, Revisions, DragHelper) ->

  new class Flow

    TEXT = 'F'
    VOICE = 'V'
    SURVEY = 'S'
    USSD = 'U'

    ALL = [TEXT,VOICE,SURVEY,USSD]
    NONE = []
    ALL_TEXT = [TEXT,SURVEY,USSD]
    ONLINE_TEXT = [TEXT,USSD]

    constructor: ->

      @actions = [
        { type:'say', name:'Play Message', verbose_name:'Play a message', icon: 'icon-bubble-3', message: true, filter:[VOICE] }
        { type:'play', name:'Play Recording', verbose_name:'Play a contact recording', icon: 'icon-mic', filter:[VOICE]}
        { type:'reply', name:'Send Message', verbose_name:'Send a response message', icon: 'icon-bubble-3', message:true, filter:ALL }
        { type:'end_ussd', name:'End USSD Session', verbose_name:'End USSD session with message', icon: 'icon-bubble-3', message:true, filter:USSD }
        { type:'send', name:'Send Message', verbose_name: 'Send a message to somebody else', icon: 'icon-bubble-3', message:true, filter:[TEXT,VOICE] }
        { type:'add_label', name:'Add Label', verbose_name: 'Add a label to a Message', icon: 'icon-tag', filter:ALL }
        { type:'save', name:'Update Contact', verbose_name:'Update the contact', icon: 'icon-user', filter:ALL }
        { type:'add_group', name:'Add to Groups', verbose_name:'Add contact to a group', icon: 'icon-users-2', groups:true, filter:ALL }
        { type:'del_group', name:'Remove from Groups', verbose_name:'Remove contact from a group', icon: 'icon-users-2', groups:true, filter:ALL }
        { type:'api', name:'Webhook', verbose_name:'Make a call to an external server', icon: 'icon-cloud-upload', filter:[TEXT,VOICE] }
        { type:'email', name:'Send Email', verbose_name: 'Send an email', icon: 'icon-bubble-3', filter:[TEXT,VOICE] }
        { type:'lang', name:'Set Language', verbose_name:'Set language for contact', icon: 'icon-language', filter:ALL }
        { type:'channel', name:'Set Channel', verbose_name:'Set preferred channel', icon: 'icon-phone', filter:[TEXT, VOICE] }
        { type:'flow', name:'Start Another Flow', verbose_name:'Start another flow', icon: 'icon-tree', flows:true, filter:[TEXT,VOICE] }
        { type:'trigger-flow',   name:'Start Someone in a Flow', verbose_name:'Start someone else in a flow', icon: 'icon-tree', flows:true, filter:[TEXT,VOICE,USSD] }
      ]

      @rulesets = [

        { type: 'wait_message', name:'Wait for Response', verbose_name: 'Wait for response', split:'message response', filter:[TEXT,SURVEY] },
        { type: 'wait_menu', name:'Wait for USSD Menu', verbose_name: 'Wait for USSD Menu', split:'USSD Menu response', filter:USSD },
        { type: 'wait_ussd', name:'Wait for USSD Response', verbose_name: 'Wait for USSD response', split:'USSD response', filter:USSD },

        # survey media types
        { type: 'wait_photo', name:'Wait for a photo', verbose_name: 'Wait for photo', filter:[SURVEY] },
        { type: 'wait_audio', name:'Wait for an audio recording', verbose_name: 'Wait for audio', filter:[SURVEY] },
        { type: 'wait_video', name:'Wait for a video', verbose_name: 'Wait for video', filter:[SURVEY] },
        { type: 'wait_gps', name:'Wait for GPS coordinates', verbose_name: 'Wait for GPS', filter:[SURVEY] },

        # voice flows only
        { type: 'wait_recording', name:'Get Recording', verbose_name: 'Wait for recording', filter:VOICE },
        { type: 'wait_digit', name:'Get Menu Selection', verbose_name: 'Wait for menu selection', filter:VOICE },
        { type: 'wait_digits', name:'Get Digits', verbose_name: 'Wait for multiple digits', split:'digits', filter:VOICE },

        # online flows
        { type: 'webhook', name:'Call Webhook', verbose_name: 'Call webhook', split:'webhook response', filter:[TEXT,VOICE,USSD], rules:[
          { name: 'Success', test: { type: 'webhook_status', status: 'success'}},
          { name: 'Failure', test: { type: 'webhook_status', status: 'failure'}},
        ]},

        { type: 'resthook', name:'Call Zapier', verbose_name: 'Call Zapier', split:'zapier response', filter:[TEXT,VOICE,USSD], rules:[
          { name: 'Success', test: { type: 'webhook_status', status: 'success'}},
          { name: 'Failure', test: { type: 'webhook_status', status: 'failure'}},
        ]},

        { type: 'airtime', name:'Transfer Airtime', verbose_name: 'Transfer Airtime', split: 'transfer airtime', filter:[TEXT, VOICE], rules: [
          { name: 'Success', test: { type: 'airtime_status', exit_status: 'success'}},
          { name: 'Failure', test: { type: 'airtime_status', exit_status: 'failed'}},
        ]},

        # all flows
        { type: 'subflow', name: 'Run Flow', verbose_name: 'Run a flow', filter: ALL, rules: [
          { name: 'Completed', test: { type: 'subflow', exit_type: 'completed' }},
          { name: 'Expired', test: { type: 'subflow', exit_type: 'expired' }}
        ]},

        { type: 'flow_field', name:'Split by Flow Field', verbose_name: 'Split by flow field', filter:ALL },
        { type: 'contact_field', name: 'Split by Contact Field', verbose_name: 'Split by contact field', filter:ALL },
        { type: 'expression', name:'Split by Expression', verbose_name: 'Split by expression', filter:ALL },
        { type: 'form_field', name:'Split by Message Form', verbose_name: 'Split by message form', filter:ALL },

        { type: 'random', name:'Random Split', verbose_name: 'Split randomly', hide_other: true, filter:ALL},

        { type: 'group', name:'Group Membership', verbose_name: 'Split by group membership', filter:ALL}

      ]

      # rule type to ruleset type they are exclusive to
      @exclusiveRules = {
        'subflow': ['subflow'],
        'timeout': ['wait_message'],
        'webhook_status': ['webhook', 'resthook'],
        'airtime_status': ['airtime'],
        'in_group': ['group']
      }

      @supportsRules = [
        'wait_message', 'wait_menu', 'wait_ussd', 'wait_digits',
        'expression', 'flow_field', 'contact_field', 'form_field'
      ]

      @operators = [
        { type: 'contains_any', name:'Contains any', verbose_name:'has any of these words', operands: 1, localized:true, filter: ALL_TEXT }
        { type: 'contains', name: 'Contains all', verbose_name:'has all of the words', operands: 1, localized:true, filter: ALL_TEXT }
        { type: 'contains_phrase', name:'Contains Phrase', verbose_name:'has the phrase', operands: 1, localized:true, filter: ONLINE_TEXT }
        { type: 'contains_only_phrase', name:'Contains only phrase', verbose_name:'has only the phrase', operands: 1, localized:true, filter: ONLINE_TEXT }
        { type: 'not_empty', name: 'Not empty', verbose_name:'is not empty', operands: 0, localized:true, filter: ALL_TEXT }
        { type: 'starts', name: 'Starts with', verbose_name:'starts with', operands: 1, localized:true, filter: ALL }
        { type: 'number', name: 'Has a number', verbose_name:'has a number', operands: 0, filter: ALL }
        { type: 'lt', name: 'Less than', verbose_name:'has a number less than', operands: 1, filter: ALL }
        { type: 'eq', name: 'Equal to', verbose_name:'has a number equal to', operands: 1, filter: ALL }
        { type: 'gt', name: 'More than', verbose_name:'has a number more than', operands: 1, filter: ALL }
        { type: 'between', name: 'Number between', verbose_name:'has a number between', operands: 2, filter: ALL }
        { type: 'date', name: 'Has date', verbose_name:'has a date', operands: 0, validate:'date', filter: ALL_TEXT }
        { type: 'date_before', name: 'Date before', verbose_name:'has a date before', operands: 1, validate:'date', filter: ALL_TEXT }
        { type: 'date_equal', name: 'Date equal to', verbose_name:'has a date equal to', operands: 1, validate:'date', filter: ALL_TEXT }
        { type: 'date_after', name: 'Date after', verbose_name:'has a date after', operands: 1, validate:'date', filter: ALL_TEXT }
        { type: 'has_email', name: 'Has email', verbose_name:'has an email address', operands: 0, filter: ONLINE_TEXT }
        { type: 'phone', name: 'Has a phone', verbose_name:'has a phone number', operands: 0, filter: ALL }
        { type: 'state', name: 'Has a state', verbose_name:'has a state', operands: 0, filter: ALL_TEXT }
        { type: 'district', name: 'Has a district', verbose_name:'has a district', operands: 1, auto_complete: true, placeholder:'@flow.state', filter: ALL_TEXT }
        { type: 'ward', name: 'Has a ward', verbose_name:'has a ward', operands: 2, operand_required: false, auto_complete: true, filter: ALL_TEXT}
        { type: 'regex', name: 'Regex', verbose_name:'matches regex', operands: 1, localized:true, filter: ALL }
        { type: 'subflow', name: 'Subflow', verbose_name:'subflow', operands: 0, filter: NONE }
        { type: 'in_group', name:'Is in group', verbose_name:'is in group', operands:0, filter: NONE }
        { type: 'airtime_status', name: 'Airtime Status', verbose_name:'airtime', operands: 0, filter: NONE }
        { type: 'webhook', name: 'Webhook', verbose_name:'webhook', operands: 0, filter: NONE }
        { type: 'webhook_status', name: 'Webhook Status', verbose_name:'webhook status', operands: 0, filter: NONE }
        { type: 'true', name: 'Other', verbose_name:'contains anything', operands: 0, filter: NONE }
        { type: 'timeout', name:'Timeout', verbose_name:'timeout', operands:0, filter: NONE }
        { type: 'interrupted_status', name:'Interrupted', verbose_name:'interrupted status', operands:0, filter: NONE }
      ]

      @opNames =
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

    $rootScope.errorDelay = quietPeriod

    # makes sure our flow doesn't have any bogus bits
    sanitizeFlow: ->

      base_language = @flow.base_language

      # iterate over all of our actionsets
      actionset_idx = @flow.action_sets.length
      while actionset_idx--
        actionset = @flow.action_sets[actionset_idx]
        action_idx = actionset.actions.length

        # and each of their actions
        while action_idx--
          action = actionset.actions[action_idx]

          # look for empty reply messages
          if action.type == 'reply'
            if not action.msg[base_language]
              actionset.actions.splice(action_idx, 1)

        # if there are no actions left in our actionset, remove us
        if actionset.actions.length == 0
          @flow.action_sets.splice(actionset_idx, 1)

    determineFlowStart: ->
      topNode = null
      # see if this node is higher than our last one
      checkTop = (node) ->
        if topNode == null || node.y < topNode.y
          topNode = node
        else if topNode == null || topNode.y == node.y
          if node.x < topNode.x
            topNode = node

      # check each node to see if they are the top
      for actionset in @flow.action_sets
        checkTop(actionset)
      for ruleset in @flow.rule_sets
        checkTop(ruleset)

      if topNode
        @flow.entry = topNode.uuid

    Flow = @
    $rootScope.$watch (->Flow.dirty), (current, prev) ->

      # if we just became dirty, trigger a save
      if current

        if not window.mutable
          $rootScope.error = "Your changes cannot be saved. You don't have permission to edit this flow."
          return

        Flow.dirty = false

        # clean our flow
        Flow.sanitizeFlow()

        # make sure we know our start point
        Flow.determineFlowStart()

        # schedule the save for a bit later in case more dirty events come in quick succession
        if $rootScope.saving
          cancelled = $timeout.cancel($rootScope.saving)

          # If we fail to cancel the current save we need to wait until the previous save completes and try again
          if not cancelled
            $timeout ->
              Flow.dirty = true
            , quietPeriod
            return

        $rootScope.saving = $timeout ->

          $rootScope.error = null

          $log.debug("Saving.")

          $http.post('/flow/json/' + Flow.flowId + '/', utils.toJson(Flow.flow)).error (data, statusCode) ->

            if statusCode == 400
              $rootScope.saving = false
              if UserVoice
                UserVoice.push(['set', 'ticket_custom_fields', {'Error': data.description}]);

              resolveObj =
                type: -> "error"
                title: -> "Error Saving"
                body: -> "Sorry, but we were unable to save your flow. Please reload the page and try again, this may clear your latest changes."
                details: -> data.description
                ok: -> 'Reload'
                hideCancel: -> true
                details: -> ''

              modalInstance = utils.openModal("/partials/modal?v=" + version, ModalController, resolveObj)

              modalInstance.result.then (reload) ->
                if reload
                  document.location.reload()
              return

            # we failed, could just be futzy internet, lets retry with backdown
            $rootScope.errorDelay += quietPeriod

            if $rootScope.errorDelay < (quietPeriod * (errorRetries + 1))
              $log.debug("Couldn't save changes, trying again in " + $rootScope.errorDelay)
              $timeout ->
                $rootScope.saving = false
                Flow.dirty = true
              , $rootScope.errorDelay
            else
              $rootScope.saving = false
              $rootScope.error = "Your changes may not be saved. Please check your network connection."
              $rootScope.errorDelay = quietPeriod

          .success (data, statusCode) ->
            $rootScope.error = null
            $rootScope.errorDelay = quietPeriod

            if data.status == 'failure'
              resolveObj =
                type: -> "error"
                title: -> "Error Saving"
                body: -> data.description
                ok: -> 'Reload'
                hideCancel: -> true
                details: -> ''
              modalInstance = utils.openModal("/partials/modal?v=" + version, ModalController, resolveObj)

              modalInstance.result.then (reload) ->
                if reload
                  document.location.reload()

            else
              # store our latest revision
              Flow.flow.metadata.revision = data.revision
              Flow.flow.metadata.saved_on = data.saved_on

              # update our auto completion options
              $http.get('/flow/completion/?flow=' + Flow.flowId).success (data) ->
                Flow.completions = data.message_completions
                Flow.function_completions = data.function_completions
                Flow.variables_and_functions = [Flow.completions...,Flow.function_completions...]

              Revisions.updateRevisions(Flow.flowId)

            $rootScope.saving = null

        , quietPeriod

    # is the rule allowed for this ruleset_type?
    isRuleAllowed: (ruleset_type, rule_type) ->
      if rule_type
        exclusives = @exclusiveRules[rule_type]
        if exclusives
          allowed = ruleset_type in exclusives
          return allowed

        # check if our ruleset has a prescribed set of rules
        rulesetConfig = @getRulesetConfig({type: ruleset_type})
        if rulesetConfig.rules
          for rule in rulesetConfig.rules
            if rule.test.type == rule_type
              return true
          return false
        return true

    getNode: (uuid) ->
      for actionset in @flow.action_sets
        if actionset.uuid == uuid
          return actionset

      for ruleset in @flow.rule_sets
        if ruleset.uuid == uuid
          return ruleset

    isPausingRuleset: (node) ->
      if not node?.actions
        return @isPausingRulesetType(node.ruleset_type)
      return false

    isUssdRuleset: (node) ->
      if not node?.actions
        return node.ruleset_type in ['wait_menu', 'wait_ussd']
      return false

    isPausingRulesetType: (ruleset_type) ->
      return ruleset_type in ['wait_message', 'wait_recording', 'wait_digit', 'wait_digits']

    # check if a potential connection would result in an invalid loop
    detectLoop: (nodeId, targetId, path=[]) ->

      node = @getNode(targetId)

      # can't go back on ourselves
      if nodeId == targetId and not @isUssdRuleset(node)
        throw new Error('Loop detected: ' + nodeId)

      # break out if our target is a pausing ruleset
      if node and (@isPausingRuleset(node) or @isUssdRuleset(node))
        return false

      # check if we just ate our tail
      if targetId in path
        throw new Error('Loop detected: ' + path + ',' + targetId)

      # add ourselves
      path = path.slice()
      path.push(targetId)


      # if we have rules, check each one
      if node?.rules
        for rule in node.rules
          if rule.destination
            @detectLoop(node.uuid, rule.destination, path)
      else
        if node?.destination
          @detectLoop(node.uuid, node.destination, path)

    isConnectionAllowed: (sourceId, targetId) ->
      return @getConnectionError(sourceId, targetId) == null

    getConnectionError: (sourceId, targetId) ->

      source = sourceId.split('_')[0]
      path = [ source ]

      try
        @detectLoop(source, targetId, path)
      catch e
        $log.debug(e.message)
        return 'Connecting these together would create an infinite loop in your flow. To connect these, make sure to pass it through an action that waits for a response.'
      return null

    # translates a string into a slug
    slugify: (label) ->
      return label.toString().toLowerCase().replace(/([^a-z0-9]+)/g, '_')

    # Get an array of current flow fields as:
    # [ { id: 'label_name', name: 'Label Name' } ]
    getFlowFields: (excludeRuleset) ->

      # find our unique set of keys
      flowFields = {}
      if @flow
        for ruleset in @flow.rule_sets
          flowFields[@slugify(ruleset.label)] = [ruleset.uuid, ruleset.label]

      # as an array
      result = []
      for id, details of flowFields
        uuid = details[0]
        label = details[1]
        if uuid != excludeRuleset?.uuid
          result.push({ id: id, text: label})

      return result

    # Takes an operand (@flow.split_on_name) and returns the
    # corresponding field object
    getFieldSelection: (fields, operand, isFlowFields) ->

      isFlow = false
      isContact = false

      # trim off @flow
      if operand
        if operand.length > 6 and operand.slice(0, 5) == '@flow'
          isFlow = true
          operand = operand.slice(6)

        # trim off @contact
        else if operand.length > 9 and operand.slice(0, 8) == '@contact'
          isContact = true
          operand = operand.slice(9)

      for field in fields
        if field.id == operand
          return field

      # if our field is missing, add our selves accordingly
      if operand and ((isFlow and isFlowFields) or (isContact and !isFlowFields))
        slugged = Flow.slugify(operand)
        field = {id:operand,  text:slugged + ' (missing)', missing:true }
        fields.push(field)
        return field

      return fields[0]

    getContactField: (ruleset) ->
      if Flow.contactFieldSearch
        return @getFieldSelection(Flow.contactFieldSearch, ruleset.operand, false)

    getFlowField: (ruleset) ->
      fields = Flow.getFlowFields(ruleset)
      return @getFieldSelection(fields, ruleset.operand, true)

    applyActivity: (node, activity) ->

      # $log.debug("Applying activity:", node, activity)
      count = 0
      if activity and activity.active and node.uuid of activity.active
        count = activity.active[node.uuid]
      node._active = count

      # our visited counts for rules
      if node._categories
        for category in node._categories
          count = 0
          if activity and activity.visited
            for source in category.sources
              key = source + ':' + category.target
              if key of activity.visited
                count += activity.visited[key]
          # $log.debug(category.name, category.target, count)
          category._visited = count

      else
        # our visited counts for actions
        count = 0
        if activity and activity.visited
          key = node.exit_uuid + ':' + node.destination
          if key of activity.visited
            count = activity.visited[key]
        node._visited = count

      return

    deriveCategories: (ruleset, base_language) ->

      categories = []
      for rule in ruleset.rules

        if not rule.uuid
          rule.uuid = uuid()

        if rule.test.type == "between"
          if not rule.category
            rule.category =
              base_language: rule.test.min + "-" + rule.test.max

        if rule.category
          rule_cat =  rule.category[base_language].toLocaleLowerCase()
          existing = (category.name[base_language].toLocaleLowerCase() for category in categories)

          # don't munge the Other category
          if rule.test.type == 'true' or rule_cat not in existing
            categories.push({name:rule.category, sources:[rule.uuid], target:rule.destination, type:rule.test.type})
          else

            for cat in categories

              # unlocalized flows just have a string name
              name = cat.name

              if base_language of cat.name
                name = cat.name[base_language]

              # if we are localized, use the base name
              if name?.toLocaleLowerCase() == rule_cat?.toLocaleLowerCase()
                cat.sources.push(rule.uuid)

                if cat.target
                  rule.destination = cat.target

      # shortcut our first source
      for cat in categories
        cat.source = cat.sources[0]

      ruleset._categories = categories
      @applyActivity(ruleset, $rootScope.visibleActivity)
      return

    markDirty: ->

      Flow = @
      $timeout ->
        Flow.dirty = true
      ,0

    # Updates a single source to a given target. Expects a source id and a target id.
    # Source can be a rule or an actionset id.
    updateDestination: (source, target) ->
      source = source.split('_')

      sourceNode = Flow.getNode(source[0])
      targetNode = Flow.getNode(target)
      if sourceNode and targetNode
        if Flow.isPausingRuleset(sourceNode) and Flow.isPausingRuleset(targetNode) and source[0] != target
          resolveObj =
            type: -> "warning"
            title: -> "Warning"
            body: -> "You've connected two steps that wait for a response. The contact will need to send more than one message to continue through the flow."
            ok: -> 'Ok'
            details: -> ''
            hideCancel: -> false
          utils.openModal("/partials/modal?v=" + version, ModalController, resolveObj)

      # We handle both UI described sources, or raw ids, trim off 'source' if its there
      if source.length > 1 and source[source.length-1] == 'source'
        source.pop()

      # its a rule source
      if source.length > 1
        for ruleset in Flow.flow.rule_sets
          if ruleset.uuid == source[0]

            # find the category we are updating
            if ruleset._categories
              for category in ruleset._categories
                if category.source == source[1]

                  # update our category target
                  category.target = target

                  # update all the rules in our category
                  for rule in ruleset.rules
                    if rule.uuid in category.sources
                      rule.destination = target
                  break

            Plumb.updateConnections(ruleset)
            break

      # its an action source
      else
        # keep our destination up to date
        for actionset in Flow.flow.action_sets
          if actionset.uuid == source[0]
            actionset.destination = target
            Plumb.updateConnection(actionset)
            @applyActivity(actionset, $rootScope.activity)
            break

    getActionConfig: (action) ->
      for cfg in @actions
        if cfg.type == action.type
          return cfg

    getRulesetConfig: (ruleset) ->
      for cfg in @rulesets
        if cfg.type == ruleset.type
          return cfg

    getOperatorConfig: (operatorType) ->
      for cfg in @operators
        if cfg.type == operatorType
          return cfg

    fetchRecentMessages: (exit_uuids, to_uuid) ->
      return $http.get('/flow/recent_messages/' + Flow.flowId + '/?exits=' + exit_uuids.join() + '&to=' + to_uuid).success (data) ->

    fetch: (flowId, onComplete = null) ->

      @flowId = flowId
      Revisions.updateRevisions(flowId)

      Flow = @
      $http.get('/flow/json/' + flowId + '/').success (data) ->

        flow = data.flow

        flow.type = window.flow_type

        # add uuids for the individual actions, need this for the UI
        for actionset in flow.action_sets
          for action in actionset.actions
            if not action.uuid
              action.uuid = uuid()

        # save the channel countries
        Flow.channel_countries = data.channel_countries

        # save away the available channels
        Flow.channels = data.channels

        languages = []

        # show our base language first
        for lang in data.languages
          if lang.iso_code == flow.base_language
            languages.push(lang)
            Flow.language = lang

        for lang in data.languages
          if lang.iso_code != flow.base_language
            languages.push(lang)

        # if they don't have our base language in the org, force ourselves as the default
        if not Flow.language and flow.base_language
          Flow.language =
            iso_code: flow.base_language

        # if we have language choices, make sure our base language is one of them
        if languages
          if flow.base_language not in (lang.iso_code for lang in languages)
            languages.unshift
              iso_code:flow.base_language
              name: gettext('Default')

        Flow.languages = languages
        Flow.flow = flow

        # fire our completion trigger if it was given to us
        if onComplete
          onComplete()

        # update our auto completion options
        $http.get('/flow/completion/?flow=' + flowId).success (data) ->
          if data.function_completions and data.message_completions
            Flow.completions = data.message_completions
            Flow.function_completions = data.function_completions
            Flow.variables_and_functions = [Flow.completions...,Flow.function_completions...]

        $http.get('/contactfield/json/').success (fields) ->
          Flow.contactFields = fields

          # now create a version that's select2 friendly
          contactFieldSearch = []
          updateContactSearch = []

          for field in fields

            id = field.key
            text = field.label

            contactFieldSearch.push({ id: id, text: text })

            if field.key == 'groups'
              continue

            if id == 'tel_e164'
              text = 'Phone Numbers'

            updateContactSearch.push({ id: id, text: text })

          Flow.contactFieldSearch = contactFieldSearch
          Flow.updateContactSearch = updateContactSearch

        $http.get('/label/').success (labels) ->
          Flow.labels = labels

        $timeout ->
          window.loaded = true
          Plumb.repaint()
        , 0

    replaceRuleset: (ruleset, markDirty=true) ->

      # make sure we don't have a cached field names
      ruleset._flowFieldName = null
      ruleset._contactFieldName = null

      # find the ruleset we are replacing by uuid
      found = false

      # if there isn't an operand, infer it
      if not ruleset.operand
        ruleset.operand = '@step.value'

      for previous, idx in Flow.flow.rule_sets
        if ruleset.uuid == previous.uuid

          # group our rules by category and update the master ruleset
          @deriveCategories(ruleset, Flow.flow.base_language)

          Flow.flow.rule_sets.splice(idx, 1, ruleset)
          found = true

          if markDirty
            @markDirty()
          break

      if not found
        Flow.flow.rule_sets.push(ruleset)
        if markDirty
          @markDirty()

      #Plumb.repaint($('#' + rule.uuid))
      Plumb.repaint()

      return

    updateTranslationStats: ->

      if @language
        # look at all translatable bits in our flow and check for completeness
        flow = @flow
        items = 0
        missing = 0
        for actionset in flow.action_sets
          for action in actionset.actions
            if action.type in ['send', 'reply', 'say']
              items++
              if action._missingTranslation
                missing++

        for ruleset in flow.rule_sets
          for category in ruleset._categories
              items++
              if category._missingTranslation
                missing++
          # ussd ruleset
          if ruleset.ruleset_type in ["wait_menu", "wait_ussd"]
            items++
            if ruleset.config._missingTranslation
              missing++
            if ruleset.ruleset_type == "wait_menu"
              for item in ruleset.rules
                items++
                if item._missingTranslation
                  missing++

        # set our stats and translation status
        flow._pctTranslated = (Math.floor(((items - missing) / items) * 100))
        flow._missingTranslation = items > 0

        if flow._pctTranslated == 100 and flow.base_language != @language.iso_code
          $rootScope.gearLinks = [
            {
              title: 'Default Language'
              id: 'default_language'
            },
            {
              id: 'divider'
            }
          ]
        else
          $rootScope.gearLinks = []

        return flow._pctTranslated

    setMissingTranslation: (missing) ->
      Flow.flow._missingTranslation = missing

    removeConnection: (connection) ->
      @updateDestination(connection.sourceId, null)

    removeRuleset: (uuid) ->

      DragHelper.hide()

      flow = Flow.flow

      Flow = @
      # disconnect all of our connections to and from the node
      $timeout ->

        # update our model to nullify rules that point to us
        connections = Plumb.getConnectionMap({ target: uuid })
        for source of connections
          Flow.updateDestination(source, null)

        # then remove us
        for rs, idx in flow.rule_sets
          if rs.uuid == uuid
            flow.rule_sets.splice(idx, 1)
            break
      ,0

      @markDirty()

    addNote: (x, y) ->

      if not Flow.flow.metadata.notes
        Flow.flow.metadata.notes = []

      Flow.flow.metadata.notes.push
        x: x
        y: y
        title: 'New Note'
        body: '...'

    removeNote: (note) ->
      idx = Flow.flow.metadata.notes.indexOf(note)
      Flow.flow.metadata.notes.splice(idx, 1)
      @markDirty()

    moveActionUp: (actionset, action) ->
      idx = actionset.actions.indexOf(action)
      actionset.actions.splice(idx, 1)
      actionset.actions.splice(idx-1, 0, action)

      # clear our last action marker
      actionset._lastActionMissingTranslation = null

      @markDirty()


    removeActionSet: (actionset) ->
      flow = Flow.flow

      service = @
      # disconnect all of our connections to and from action node
      $timeout ->

        # update our model to nullify rules that point to us
        connections = Plumb.getConnectionMap({ target: actionset.uuid })
        for source of connections
          service.updateDestination(source, null)

        # disconnect our connections, then remove it from the flow
        # Plumb.disconnectAllConnections(actionset.uuid)
        for as, idx in flow.action_sets
          if as.uuid == actionset.uuid
            flow.action_sets.splice(idx, 1)
            break
      ,0


    removeAction: (actionset, action) ->

      DragHelper.hide()

      found = false
      for previous, idx in actionset.actions
        if previous.uuid == action.uuid
          actionset.actions.splice(idx, 1)
          found = true
          break

      if found

        # if there are no actions left, remove our node
        if actionset.actions.length == 0
          @removeActionSet(actionset)
        else
          # if we still have actions, make sure our connection offsets are correct
          Plumb.recalculateOffsets(actionset.uuid)

        @checkTerminal(actionset)
        @markDirty()

      return

    checkTerminal: (actionset) ->

      hasMessage = false
      startsFlow = false

      for action in actionset.actions
        if action.type == 'flow'
          startsFlow = true

      # if they start another flow it's terminal
      terminal = startsFlow

      if actionset._terminal != terminal
        actionset._terminal = terminal

    isMoveableAction: (action) ->
      if not action
        return true

      return action.type not in ['flow', 'end_ussd']

    saveAction: (actionset, action) ->

      actionset._lastActionMissingTranslation = null

      if action.type == "end_ussd"
        actionset.destination = null
        Plumb.updateConnection(actionset)
        @applyActivity(actionset, $rootScope.activity)

      found = false
      lastAction = null
      for previous, idx in actionset.actions
        lastAction = previous
        if previous.uuid == action.uuid

          # force immovable actions down
          if not @isMoveableAction(action)
            actionset.actions.splice(idx, 1)
            actionset.actions.push(action)
            found = true
          else
            actionset.actions.splice(idx, 1, action)
            found = true
          break

      # if there isn't one that matches add a new one
      if not found
        action.uuid = uuid()

        # if our last action isn't moveable go above it
        if not @isMoveableAction(lastAction)
          actionset.actions.splice(actionset.actions.length-1, 0, action)
        else
          actionset.actions.push(action)

      Plumb.recalculateOffsets(actionset.uuid)

      # finally see if our actionset exists or if it needs to be added
      found = false
      for as in Flow.flow.action_sets
        if as.uuid == actionset.uuid
          found = true
          break

      if not found
        Flow.flow.action_sets.push(actionset)

      if Flow.flow.action_sets.length == 1
        if not Flow.flow.action_sets[0].destination
          $timeout ->
            DragHelper.showSaveResponse($('#' + Flow.flow.action_sets[0].uuid + ' .source'))
          ,0

      @checkTerminal(actionset)
      @markDirty()

]

ModalController = ($scope, $modalInstance, type, title, body, hideCancel=false, details=null, ok=null) ->
  $scope.type = type
  $scope.title = title
  $scope.body = body
  $scope.error = error
  $scope.details = details
  $scope.hideCancel = hideCancel

  if ok
    $scope.okButton = ok
    $scope.ok = ->
      $modalInstance.close true
  else
    $scope.okButton = "Ok"
    $scope.ok = ->
      $modalInstance.dismiss "cancel"

  $scope.cancel = ->
    $modalInstance.dismiss "cancel"

  $scope.showHelpWidget = ->
    if UserVoice
      UserVoice.push(['show', {
        mode: 'contact'
      }]);
