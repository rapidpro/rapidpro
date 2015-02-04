app = angular.module('temba.services', [])

version = new Date().getTime()

quietPeriod = 1000
errorRetries = 5

app.service "utils", ->

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

  makeSource: (element, scope) ->
    jsPlumb.makeSource element, sourceDefaults,
      scope: scope

  makeTarget: (element, scope) ->
    jsPlumb.makeTarget element, targetDefaults,
      scope: scope

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

    # update ourselves
    jsPlumb.recalculateOffsets(nodeId)
    jsPlumb.repaint(nodeId)

    # do the same for all of our sources
    $('#' + nodeId + ' .source').each ->
      jsPlumb.recalculateOffsets(this)
      jsPlumb.repaint(this)


  disconnectAllConnections: (id) ->
    jsPlumb.detachAllConnections(id)

    $('#' + id + ' .source').each ->
      jsPlumb.detachAllConnections($(this))

  disconnectOutboundConnections: (id) ->
    jsPlumb.detachAllConnections($('#' + id + ' .source'))

  setSourceEnabled: (source, enabled) ->
    jsPlumb.setSourceEnabled(source, enabled)

  connect: (sourceId, targetId, scope, fireEvent = true) ->

    # remove any existing connections for our source first
    @disconnectOutboundConnections(sourceId)

    # connect to our new target if we have one
    if targetId != null

      target = $('#' + targetId)
      existing = jsPlumb.getEndpoints(target)
      targetPoint = null
      if existing
        for endpoint in existing
          if endpoint.connections.length == 0
            targetPoint = existing[0]
            break

      if not targetPoint
        targetPoint = jsPlumb.addEndpoint(target, { scope: scope }, targetDefaults)

      source = $('#' + sourceId + ' .source')

      if jsPlumb.getConnections({source:source, scope:scope}).length == 0

        # make sure our source is enabled before attempting connection
        jsPlumb.setSourceEnabled(source, true)

        jsPlumb.connect({ maxConnections:1, dragAllowedWhenFull:false, deleteEndpointsOnDetach:true, editable:false, source: source, target: targetPoint, fireEvent: fireEvent})

        # now that we are connected, we aren't enabled anymore
        jsPlumb.setSourceEnabled(source, false)


  updateConnection: (actionset) ->
    if actionset.destination
      @connect(actionset.uuid, actionset.destination, 'rules')

    $timeout ->
      jsPlumb.recalculateOffsets(actionset.uuid)
      jsPlumb.repaint(actionset.uuid)
    , 0

  updateConnections: (ruleset) ->
    for category in ruleset._categories
      if category.target
        @connect(category.sources[0], category.target, 'actions')

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
        #$log.debug("Plumb.repaint(ele)")
        jsPlumb.repaint(element)
      else
        #$log.debug("Plumb.repaint()")
        jsPlumb.repaintEverything()

      service.setPageHeight()
    , 0

  disconnectRules: (rules) ->
    for rule in rules
      jsPlumb.remove(rule.uuid)

  getConnectionMap: (selector = {}) ->

    # get the current connections as a map
    connections = {}
    jsPlumb.select(selector).each (connection) ->

      # only count legitimate targets
      if connection.targetId and connection.targetId.length > 24
        # find the source
        ele = $(connection.source)
        source = ele.parents('.rule')
        if not source.length
          source = ele.parents('.node')

        # if we found a source add it to our map
        if source.length
          connections[source.attr('id')] = connection.targetId

    return connections
]

app.service "Versions", ['$rootScope', '$http', '$log', ($rootScope, $http, $log) ->
  updateVersions: ->
    $http.get('/flow/versions/' + $rootScope.flowId).success (data, status, headers) ->

      # only set the versions if we get back json, if we don't have permission we'll get a login page
      if headers('content-type') == 'application/json'
        $rootScope.versions = data
]

app.service "Flow", ['$rootScope', '$window', '$http', '$timeout', '$interval', '$log', '$modal', 'utils', 'Plumb', 'Versions', 'DragHelper', ($rootScope, $window, $http, $timeout, $interval, $log, $modal, utils, Plumb, Versions, DragHelper) ->

  $rootScope.actions = [
    { type:'say', name:'Play Message', verbose_name:'Play a message', icon: 'icon-bubble-3', message: true }
    { type:'play', name:'Play Recording', verbose_name:'Play a contact recording', icon: 'icon-mic'}
    { type:'reply', name:'Send Message', verbose_name:'Send an SMS response', icon: 'icon-bubble-3', message:true }
    { type:'send', name:'Send Message', verbose_name: 'Send an SMS to somebody else', icon: 'icon-bubble-3', message:true }
    { type:'add_label', name:'Add Label', verbose_name: 'Add a label to a Message', icon: 'icon-tag' }
    { type:'save', name:'Update Contact', verbose_name:'Update the contact', icon: 'icon-user'}
    { type:'add_group', name:'Add to Groups', verbose_name:'Add contact to a group', icon: 'icon-users-2', groups:true }
    { type:'del_group', name:'Remove from Groups', verbose_name:'Remove contact from a group', icon: 'icon-users-2', groups:true }
    { type:'api', name:'Webhook', verbose_name:'Make a call to an external server', icon: 'icon-cloud-upload' }
    { type:'email', name:'Send Email', verbose_name: 'Send an email', icon: 'icon-bubble-3' }
    { type:'lang', name:'Set Language', verbose_name:'Set language for contact', icon: 'icon-language'}
    { type:'flow', name:'Start Another Flow', verbose_name:'Start another flow', icon: 'icon-tree', flows:true }
    { type:'trigger-flow',   name:'Start Someone in a Flow', verbose_name:'Start someone else in a flow', icon: 'icon-tree', flows:true }
  ]

  $rootScope.operators = [
    { type:'contains_any', name:'Contains any', verbose_name:'has any of these words', operands: 1, localized:true }
    { type:'contains', name: 'Contains all', verbose_name:'has all of the words', operands: 1, localized:true }
    { type:'starts', name: 'Starts with', verbose_name:'starts with', operands: 1, voice:true, localized:true }
    { type:'number', name: 'Has a number', verbose_name:'has a number', operands: 0, voice:true }
    { type:'lt', name: 'Less than', verbose_name:'has a number less than', operands: 1, voice:true }
    { type:'eq', name: 'Equal to', verbose_name:'has a number equal to', operands: 1, voice:true }
    { type:'gt', name: 'More than', verbose_name:'has a number more than', operands: 1, voice:true }
    { type:'between', name: 'Number between', verbose_name:'has a number between', operands: 2, voice:true }
    { type:'date', name: 'Has date', verbose_name:'has a date', operands: 0, validate:'date' }
    { type:'date_before', name: 'Date before', verbose_name:'has a date before', operands: 1, validate:'date' }
    { type:'date_equal', name: 'Date equal to', verbose_name:'has a date equal to', operands: 1, validate:'date' }
    { type:'date_after', name: 'Date after', verbose_name:'has a date after', operands: 1, validate:'date' }
    { type:'phone', name: 'Has a phone', verbose_name:'has a phone number', operands: 0, voice:true }
    { type:'state', name: 'Has a state', verbose_name:'has a state', operands: 0 }
    { type:'district', name: 'Has a district', verbose_name:'has a district', operands: 1, auto_complete: true, placeholder:'@flow.state' }
    { type:'regex', name: 'Regex', verbose_name:'matches regex', operands: 1, voice:true, localized:true }
    { type:'true', name: 'Other', verbose_name:'contains anything', operands: 0 }
  ]

  $rootScope.opNames =
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

  $rootScope.$watch (->$rootScope.dirty), (current, prev) ->

    # if we just became dirty, trigger a save
    if current

      if not window.mutable
        $rootScope.error = "Your changes cannot be saved. You don't have permission to edit this flow."
        return

      $rootScope.dirty = false

      # make sure we know our start point
      determineFlowStart($rootScope.flow)

      # schedule the save for a bit later in case more dirty events come in quick succession
      if $rootScope.saving
        cancelled = $timeout.cancel($rootScope.saving)

        # If we fail to cancel the current save we need to wait until the previous save completes and try again
        if not cancelled
          $timeout ->
            $rootScope.dirty = true
          , quietPeriod
          return

      $rootScope.saving = $timeout ->

        $rootScope.error = null

        $log.debug("Saving.")

        if $rootScope.saved_on
          $rootScope.flow['last_saved'] = $rootScope.saved_on

        $http.post('/flow/json/' + $rootScope.flowId + '/', utils.toJson($rootScope.flow)).error (data) ->
          $log.debug("Failed:", data)
          $rootScope.errorDelay += quietPeriod

          # we failed, could just be futzy internet, lets retry with backdown
          if $rootScope.errorDelay < (quietPeriod * (errorRetries + 1))
            $log.debug("Couldn't save changes, trying again in " + $rootScope.errorDelay)
            $timeout ->
              $rootScope.dirty = true
            , $rootScope.errorDelay
          else
            $rootScope.saving = false
            $rootScope.error = "Your changes may not be saved. Please check your network connection."
            $rootScope.errorDelay = quietPeriod

        .success (data) ->
          $rootScope.error = null
          $rootScope.errorDelay = quietPeriod
          if data.status == 'unsaved'
            modalInstance = $modal.open
              templateUrl: "/partials/modal?v=" + version
              controller: ModalController
              resolve:
                type: -> "error"
                title: -> "Editing Conflict"
                body: -> data.saved_by + " is currently editing this Flow. Your changes will not be saved until the Flow is reloaded."
                ok: -> 'Reload'

            modalInstance.result.then (reload) ->
              if reload
                document.location.reload()

          else
            $rootScope.saved_on = data.saved_on

            # update our auto completion options
            $http.get('/flow/completion/?flow=' + $rootScope.flowId).success (data) ->
              $rootScope.completions = data

            Versions.updateVersions()

          $rootScope.saving = null

      , quietPeriod

  determineFlowStart = (flow) ->
    topX = null
    topY = null
    entry = null
    $('#flow > .node').each ->
      ele = $(this)
      if not ele.hasClass('ghost')
        x = ele[0].offsetLeft
        y = ele[0].offsetTop
        if topY == null || y < topY
          topY = y
          topX = x
          entry = ele.attr('id')

        else if topY == y
          if topX == null || x < topX
            topY = y
            topX = x
            entry = ele.attr('id')
    flow.entry = entry

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
      key = node.uuid + ':' + node.destination
      count = 0
      if activity and activity.visited and key of activity.visited
        count += activity.visited[key]
      node._visited = count

    return

  deriveCategories: (ruleset, language) ->
    ruleset._categories = []
    for rule in ruleset.rules

      if not rule.uuid
        rule.uuid = uuid()

      if rule.test.type == "between"
        if not rule.category
          if $rootScope.flow.base_language
            rule.category = {}
            rule.category[language] = rule.test.min + " - " + rule.test.max
          else
            rule.category = rule.test.min + " - " + rule.test.max

      if rule.category
        if $rootScope.flow.base_language
          rule_cat = rule.category[language]
          existing = (category.name[language] for category in ruleset._categories)
        else
          rule_cat = rule.category
          existing = (category.name for category in ruleset._categories)

        if rule_cat not in existing
          ruleset._categories.push({name:rule.category, sources:[rule.uuid], target:rule.destination, type:rule.test.type})
        else
          for cat in ruleset._categories
            if cat.name == rule_cat
              cat.sources.push(rule.uuid)
              if cat.target
                rule.destination = cat.target

    @applyActivity(ruleset, $rootScope.activity)
    return

  determineFlowStart: ->
    determineFlowStart($rootScope.flow)

  markDirty: ->
    $timeout ->
      $rootScope.dirty = true
    ,0

  getActionConfig: (action) ->
    for cfg in $rootScope.actions
      if cfg.type == action.type
        return cfg

  getOperatorConfig: (operatorType) ->
    for cfg in $rootScope.operators
      if cfg.type == operatorType
        return cfg

  fetch: (onComplete = null) ->

    # here's where we bridge from our initial load into angular land
    $rootScope.flowId = $window.flowId

    Versions.updateVersions()

    $http.get('/flow/json/' + $rootScope.flowId + '/').success (data) ->

      # create a unique set of categories
      flow = data.flow

      for actionset in flow.action_sets
        for action in actionset.actions
          action.uuid = uuid()

      languages = []

      # show our base language first
      for lang in data.languages
        if lang.iso_code == flow.base_language
          languages.push(lang)
          $rootScope.language = lang

      for lang in data.languages
        if lang.iso_code != flow.base_language
          languages.push(lang)


      $rootScope.languages = languages
      $rootScope.flow = flow

      # fire our completion trigger if it was given to us
      if onComplete
        onComplete()

      # update our auto completion options
      $http.get('/flow/completion/?flow=' + $rootScope.flowId).success (data) ->
        $rootScope.completions = data

      $http.get('/contactfield/json/').success (fields) ->
        $rootScope.contactFields = fields

        # now create a version that's select2 friendly
        contactFieldSearch = []

        contactFieldSearch.push
           id: "name"
           text: "Contact Name"

        for field in fields
          contactFieldSearch.push
            id: field.key
            text: field.label
        $rootScope.contactFieldSearch = contactFieldSearch

      $http.get('/label/').success (labels) ->
        $rootScope.labels = labels

      $timeout ->
        window.loaded = true
        Plumb.repaint()
      , 0

  replaceRuleset: (ruleset, markDirty=true) ->

    # $log.debug("Replacing ruleset: ", ruleset)

    # find the ruleset we are replacing by uuid
    found = false
    for previous, idx in $rootScope.flow.rule_sets
      if ruleset.uuid == previous.uuid

        # remove the existing connections from the rules, these will
        # be recreated by our watcher when the ruleset changes below
        for rule in previous.rules
          oldSource = $('#' + rule.uuid + " .source")
          jsPlumb.detachAllConnections(oldSource)

        # group our rules by category and update the master ruleset
        @deriveCategories(ruleset, $rootScope.flow.base_language)

        $rootScope.flow.rule_sets.splice(idx, 1, ruleset)
        found = true

        if markDirty
          @markDirty()

    if not found
      $rootScope.flow.rule_sets.push(ruleset)
      if markDirty
        @markDirty()

    #Plumb.repaint($('#' + rule.uuid))
    Plumb.repaint()

    return

  removeConnection: (connection) ->
    node = $(connection.source).parents('.node').attr('id')
    rule = $(connection.source).parents('.rule').attr('id')

    if connection.scope == 'actions'
      @updateRuleTarget(node, rule, null)

    if connection.scope == 'rules'
      @updateActionsTarget(node, null)

    Plumb.detachSingleConnection(connection)

  removeRuleset: (ruleset) ->

    DragHelper.hide()

    flow = $rootScope.flow

    service = @
    # disconnect all of our connections to and from the node
    $timeout ->

      # update our model to nullify rules that point to us
      connections = Plumb.getConnectionMap({ target: ruleset.uuid })
      for from of connections
        service.updateActionsTarget(from, null)

      # disconnect our connections, then remove it from the flow
      Plumb.disconnectAllConnections(ruleset.uuid)
      idx = flow.rule_sets.indexOf(ruleset)
      flow.rule_sets.splice(idx, 1)
    ,0

    @markDirty()

  addNote: (x, y) ->
    $rootScope.flow.metadata.notes.push
      x: x
      y: y
      title: 'New Note'
      body: '...'

  removeNote: (note) ->
    idx = $rootScope.flow.metadata.notes.indexOf(note)
    $rootScope.flow.metadata.notes.splice(idx, 1)
    @markDirty()

  moveActionUp: (actionset, action) ->
    idx = actionset.actions.indexOf(action)
    actionset.actions.splice(idx, 1)
    actionset.actions.splice(idx-1, 0, action)
    @markDirty()


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
        flow = $rootScope.flow

        service = @
        # disconnect all of our connections to and from action node
        $timeout ->

          # update our model to nullify rules that point to us
          connections = Plumb.getConnectionMap({ target: actionset.uuid })
          for from of connections
            node = $("#" + from).parents('.node').attr('id')
            service.updateRuleTarget(node, from, null)

          # disconnect our connections, then remove it from the flow
          Plumb.disconnectAllConnections(actionset.uuid)
          idx = flow.action_sets.indexOf(actionset)
          flow.action_sets.splice(idx, 1)

        ,0

      else
        # if we still have actions, make sure our connection offsets are correct
        $timeout ->
          Plumb.recalculateOffsets(actionset.uuid)
        ,0

      @checkTerminal(actionset)
      @markDirty()

    return

  updateActionsTarget: (from, to) ->
    for actionset in $rootScope.flow.action_sets
      if actionset.uuid == from
        actionset.destination = to
        @applyActivity(actionset, $rootScope.activity)
        break

  updateRuleTarget: (node, from, to) ->

    for ruleset in $rootScope.flow.rule_sets
      if ruleset.uuid == node

        for rule in ruleset.rules
          if rule.uuid == from
            rule.destination = to

        for category in ruleset._categories
          for source in category.sources
            if source == from
              category.target = to

        @applyActivity(ruleset, $rootScope.activity)

  checkTerminal: (actionset) ->
    terminal = true
    for action in actionset.actions
      if window.ivr and action.type == 'say'
        terminal = false
        break

      if not window.ivr and action.type == 'reply'
        terminal = false
        break

    if actionset._terminal != terminal
      actionset._terminal = terminal

  saveAction: (actionset, action) ->

    found = false
    for previous, idx in actionset.actions
      if previous.uuid == action.uuid
        actionset.actions.splice(idx, 1, action)
        found = true
        break

    # if there isn't one that matches add a new one
    if not found
      action.uuid = uuid()
      actionset.actions.push(action)

    #$log.debug("Adding new action", actionset)

    # finally see if our actionset exists or if it needs to be added
    found = false
    for as in $rootScope.flow.action_sets
      if as.uuid == actionset.uuid
        found = true
        break

    if not found
      $rootScope.flow.action_sets.push(actionset)

    if $rootScope.flow.action_sets.length == 1
      $timeout ->
        DragHelper.showSaveResponse($('#' + $rootScope.flow.action_sets[0].uuid + ' .source'))
      ,0

    @checkTerminal(actionset)
    @markDirty()
]

ModalController = ($scope, $modalInstance, type, title, body, ok=null) ->
  $scope.type = type
  $scope.title = title
  $scope.body = body

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
