#============================================================================
# Our main controller. Manages our flow state.
#============================================================================
app = angular.module('temba.controllers', ['ui.bootstrap', 'temba.services', 'ngAnimate'])

version = new Date().getTime()

app.controller 'VersionController', [ '$scope', '$rootScope', '$log', '$timeout', 'Flow', ($scope, $rootScope, $log, $timeout, Flow) ->

  # apply the current flow as our definition
  $scope.apply = ->
    $scope.applyDefinition($rootScope.flow)

  # go back to our original version
  $scope.cancel = ->
    if $rootScope.original
      $scope.applyDefinition($rootScope.original)
    else
      $scope.hideVersions()

  # Select the version to show
  $scope.showVersion = (version) ->

    # show our version selection
    for other in $rootScope.versions
      other.selected = false
    version.selected = true

    # store our original definition
    if not $rootScope.original
      $rootScope.original = $rootScope.flow

    # show the version definition
    $scope.showDefinition(version.definition)

  # Show a definition from a version or our original definition
  $scope.showDefinition = (definition, onChange) ->
    $rootScope.visibleActivity = false
    $rootScope.flow = null
    jsPlumb.reset()
    $timeout ->
      $rootScope.flow = definition
      if onChange
        onChange()
    ,0


  # Apply the definition and hide the revision history interface
  $scope.applyDefinition = (definition) ->

    for actionset in definition.action_sets
        for action in actionset.actions
          action.uuid = uuid()

    # remove all version selection
    for other in $rootScope.versions
      other.selected = false

    markDirty = false
    if definition != $rootScope.original
      definition.last_saved = $rootScope.original.last_saved
      markDirty = true

    $scope.showDefinition definition, ->
      $scope.hideVersions()
      # save if things have changed
      if markDirty
        Flow.markDirty()

  $scope.hideVersions = ->
      $rootScope.original = null
      $rootScope.visibleActivity = true
      $rootScope.showVersions = false


]

app.controller 'FlowController', [ '$scope', '$rootScope', '$timeout', '$modal', '$log', '$interval', '$upload', 'Flow', 'Plumb', 'DragHelper', 'utils', ($scope, $rootScope, $timeout, $modal, $log, $interval, $upload, Flow, Plumb, DragHelper, utils) ->

  # inject into our gear menu
  $rootScope.gearLinks = []
  $rootScope.ivr = window.ivr

  # when they click on an injected gear item
  $scope.clickGearMenuItem = (id) ->

    # setting our default language
    if id == 'default_language'
      modal = new ConfirmationModal(gettext('Default Language'), gettext('The default language for the flow is used for contacts which have no preferred language. Are you sure you want to set the default language for this flow to') + ' <span class="attn">' + $rootScope.language.name + "</span>?")
      modal.addClass('warning')
      modal.setListeners
        onPrimary: ->
          $scope.setBaseLanguage($rootScope.language)
      modal.show()

    return false


  $rootScope.activityInterval = 5000

  # fetch our flow to get started
  Flow.fetch ->
    $scope.updateActivity()

  showDialog = (title, body, okButton='Okay', hideCancel=true) ->

    return $modal.open
      templateUrl: "/partials/modal?v=" + version
      controller: SimpleMessageController
      resolve:
        title: -> title
        body: -> body
        okButton: -> okButton
        hideCancel: -> hideCancel


  $scope.getAcceptedScopes = (nodeType) ->
    if not window.ivr
      return 'actions rules'

    if nodeType == "ruleset"
      if window.ivr
        return 'rules'
    else
        return 'actions'


  $scope.showRevisionHistory = ->
    $scope.$evalAsync ->
      $rootScope.showVersions = true

  $scope.setBaseLanguage = (lang) ->

    # now we have a real base language, remove the default placeholder
    if $rootScope.languages[0].name == gettext('Default')
      $rootScope.languages.splice(0, 1)

    # reorder our languages so the base language is first
    $rootScope.languages.splice($rootScope.languages.indexOf(lang), 1)
    $rootScope.languages.unshift(lang)


    # set the base language
    $rootScope.flow.base_language = lang.iso_code

    $timeout ->
      $scope.setLanguage(lang)
      Flow.markDirty()
    ,0


  # Handle uploading of audio files for IVR recorded prompts
  $scope.onFileSelect = ($files, actionset, action) ->

    if window.dragging or not window.mutable
      return

    scope = @
    # enforce just one file
    if $files.length > 1
      showDialog("Too Many Files", "To upload a sound file, please drag and drop one file for each step.")
      return

    # make sure its an audio file
    file = $files[0]
    if file.type != 'audio/wav' and file.type != 'audio/x-wav'
      showDialog('Wrong File Type', 'Audio files need to in the WAV format. Please choose a WAV file and try again.')
      return

    # if we have a recording already, confirm they want to replace it
    if action._translation_recording
      modal = showDialog('Overwrite Recording', 'This step already has a recording, would you like to replace this recording with ' + file.name + '?', 'Overwrite Recording', false)
      modal.result.then (value) ->
        if value == 'ok'
          action._translation_recording = null
          scope.onFileSelect($files, actionset, action)
      return

    action.uploading = true
    $scope.upload = $upload.upload
      url: window.uploadURL
      data:
        actionset: actionset.uuid
        action: action.uuid
      file: file
    .progress (evt) ->
      $log.debug("percent: " + parseInt(100.0 * evt.loaded / evt.total))
      return
    .success (data, status, headers, config) ->


      if $rootScope.flow.base_language
        if not action.recording
          action.recording = {}
        action.recording[$rootScope.language.iso_code] = data['path']
      else
        action.recording = data['path']

      # make sure our translation state is updated
      action.uploading = false
      action.dirty = true
      Flow.saveAction(actionset, action)
      return

    return

  $scope.scheduleActivityUpdate = ->

    $timeout ->
      $scope.updateActivity()
    , $rootScope.activityInterval

    # degrade the activity interval to deal with inactive clients
    $rootScope.activityInterval += 200

  $scope.setLanguage = (lang) ->
    Flow.setMissingTranslation(false)
    $rootScope.language = lang
    Plumb.repaint()

  $scope.updateActivity = ->

    # activity from simulation is updated separately
    if window.simulation
      $scope.scheduleActivityUpdate()
      return

    $.ajax(
      type: "GET"
      url: activityURL
      cache: false
      success: (data, status, xhr) ->

        $rootScope.pending = data.pending

        # to be successful we should be a 200 with activity data
        if xhr.status == 200 and data.activity
          $rootScope.activity =
            active: data.activity
            visited: data.visited

          if not window.simulation
            $rootScope.visibleActivity = $rootScope.activity

          $scope.scheduleActivityUpdate()

      error: (status) ->
        console.log("Error:")
        console.log(status)
    )


  $scope.$watch (->$rootScope.flow), (current) ->

    if current
      jsPlumb.bind('connectionDrag', (connection) -> $scope.onConnectorDrag(connection))
      jsPlumb.bind('connectionDragStop', (connection) -> $scope.onConnectorDrop(connection))
      jsPlumb.bind('beforeDrop', (sourceId, targetId) -> $scope.onBeforeConnectorDrop(sourceId, targetId))
    else
      jsPlumb.unbind('connectionDrag')
      jsPlumb.unbind('connectionDragStop')

  $scope.$watch (->$rootScope.visibleActivity), ->
    if $rootScope.visibleActivity
      for node in $rootScope.flow.rule_sets.concat $rootScope.flow.action_sets
        Flow.applyActivity(node, $rootScope.visibleActivity)
    return

  # our categories can be combined rules
  # we always defer to the first one, this is used for plumbing
  $scope.getSource = (category) ->
    return category.sources[0]

  $scope.onBeforeConnectorDrop = (props) ->
    if not Flow.isConnectionAllowed($rootScope.flow, props.sourceId, props.targetId)
      $rootScope.ghost.hide()
      $rootScope.ghost = null
      return false
    return true

  $scope.onConnectorDrop = (connection) ->

    $(connection.sourceId).parent().removeClass('reconnecting')

    source = connection.sourceId.split('_')

    createdNewNode = false
    if $rootScope.ghost
      ghost = $rootScope.ghost
      targetId = uuid()

      # if we aren't colliding, let's make our ghost real
      if not ghost.hasClass('collision')
        if ghost.hasClass('actions')

          msg = ''
          if $rootScope.flow.base_language
            msg[$rootScope.flow.base_language] = ''

          actionset =
            x: ghost[0].offsetLeft
            y: ghost[0].offsetTop
            uuid: targetId
            actions: [
              type: if window.ivr then 'say' else 'reply'
              msg: msg
              uuid: uuid()
            ]

          $scope.clickAction(actionset, actionset.actions[0], connection.sourceId)
          createdNewNode = true

        else

          category = "All Responses"
          if $rootScope.flow.base_language
            category = {}
            category[$rootScope.flow.base_language] = "All Responses"

          ruleset =
            x: ghost[0].offsetLeft
            y: ghost[0].offsetTop
            uuid: targetId,
            label: "Response " + ($rootScope.flow.rule_sets.length + 1)
            operand: "@step.value"
            webhook_action: null,
            rules: [
              test:
                test: "true"
                type: "true"
              category: category
              uuid: uuid()
            ]

          $scope.clickRuleset(ruleset, source[0])
          createdNewNode = true

      # TODO: temporarily let ghost stay on screen with connector until dialog is closed
      $rootScope.ghost.hide()
      $rootScope.ghost = null

    if not createdNewNode

      to = connection.targetId

      # When we make a bad drop, jsplumb will give us a sourceId but no source
      if not connection.source
        to = null

    $timeout ->
      Flow.updateDestination(connection.sourceId, to)
      Flow.markDirty()
    ,0

  $scope.onConnectorDrag = (connection) ->

    DragHelper.hide()

    # add some css to our source so we can style during moves
    $(connection.sourceId).parent().addClass('reconnecting')

    scope = jsPlumb.getSourceScope(connection.sourceId)
    $rootScope.ghost = $('.ghost.' + scope)
    $timeout ->
      $rootScope.ghost.show()
    ,0

  $scope.createFirstAction = ->

    msg = ''
    if $rootScope.flow.base_language
      msg = {}
      msg[$rootScope.flow.base_language] = ''

    actionset =
      x: 100
      y: 0
      uuid: uuid()
      actions: [
        uuid: uuid()
        type: if window.ivr then 'say' else 'reply'
        msg: msg
      ]

    @clickAction(actionset, actionset.actions[0])

  # filter for translation menu
  $scope.notBaseLanguageFilter = (lang) ->
    return lang.iso_code != $scope.flow.base_language

  $scope.translatableRuleFilter = (rule) ->
    return rule.type == 'contains_any'

  # method to determine if the last action in an action set is missing a translation
  # this is necessary to style the bottom of the action set node container accordingly
  $scope.lastActionMissingTranslation = (actionset) ->
    lastAction = actionset.actions[actionset.actions.length - 1]
    if $scope.$parent.flow.base_language
      if $scope.$parent.flow.base_language != $scope.$parent.language.iso_code
        if lastAction.msg and lastAction.type in ['reply', 'send', 'send', 'say'] and not lastAction.msg[$scope.$parent.language.iso_code]
          return true

  $scope.broadcastToStep = (uuid) ->
    window.broadcastToNode(uuid)

  $scope.addNote = (event) ->
    Flow.addNote(event.offsetX, event.offsetY)

  $scope.removeNote = (note) ->
    Flow.removeNote(note)

  $scope.clickWebhook = (ruleset) ->

    DragHelper.hide()

    if window.dragging or not window.mutable
      return

    modal = $modal.open
      templateUrl: "/partials/rule_webhook?v=" + version
      controller: RuleOptionsController
      resolve:
        type: -> 'api'
        methods: -> ['GET', 'POST']
        ruleset: -> ruleset

  $scope.clickRuleset = (ruleset, dragSource=null) ->
    if window.dragging or not window.mutable
      return

    DragHelper.hide()

    if $scope.$parent.flow.base_language and $scope.$parent.flow.base_language != $scope.$parent.language.iso_code
      $modal.open
        templateUrl: "/partials/translate_rules?v=" + version
        controller: TranslateRulesController
        resolve:
          languages: ->
            from: $scope.$parent.flow.base_language
            to: $scope.$parent.language.iso_code
          ruleset: -> ruleset
    else

      if window.ivr
        $modal.open
          templateUrl: "/partials/node_editor?v=" + version
          controller: NodeEditorController
          resolve:
            scope: $scope
            options: ->
              nodeType: 'ivr'
              ruleset: ruleset
              dragSource: dragSource

      else
        $modal.open
          templateUrl: "/partials/node_editor?v=" + version
          controller: NodeEditorController
          resolve:
            options: ->
              nodeType: 'rules'
              ruleset: ruleset
              dragSource: dragSource

  $scope.confirmRemoveWebhook = (event, ruleset) ->

    if window.dragging or not window.mutable
      return

    removeWarning = $(event.target).parent().children('.remove-warning')

    # if our warning is already visible, go ahead and delete
    if removeWarning.is(':visible')
      ruleset.webhook = null
      ruleset.webhook_action = null
      Plumb.repaint()
      Flow.markDirty()

    # otherwise warn the user first
    else
      removeWarning.fadeIn()
      $timeout ->
        removeWarning.fadeOut()
      , 1500

    # important not to have coffee do an implicit return here
    # since we are mucking with jquery directly
    return false


  $scope.confirmRemoveRuleset = (event, ruleset) ->

    if window.dragging or not window.mutable
      return

    removeWarning = $(event.target).parent().children('.remove-warning')

    # if our warning is already visible, go ahead and delete
    if removeWarning.is(':visible')
      Flow.removeRuleset(ruleset)

    # otherwise warn the user first
    else
      removeWarning.fadeIn()
      $timeout ->
        removeWarning.fadeOut()
      , 1500

    # important not to have coffee do an implicit return here
    # since we are mucking with jquery directly
    return false

  $scope.confirmRemoveConnection = (connection) ->
    modal = new ConfirmationModal(gettext('Remove'), gettext('Are you sure you want to remove this connection?'))
    modal.addClass('alert')
    modal.setListeners
      onPrimary: ->
        Flow.removeConnection(connection)
        Flow.markDirty()
    modal.show()

    return false

  $scope.clickActionSource = (actionset) ->
    if actionset._terminal
      $modal.open
        templateUrl: "/partials/modal?v=" + version
        controller: TerminalWarningController
        resolve:
          actionset: -> actionset
          flowController: -> $scope
    else
      if window.mutable

        source = $("#" + actionset.uuid + "> .source")

        connection = Plumb.getSourceConnection(source)
        if connection
          $scope.confirmRemoveConnection(connection)
        else
          $timeout ->
            DragHelper.showSaveResponse($('#' + actionset.uuid + ' .source'))
          ,0

  $scope.clickRuleSource = (category) ->
    if window.mutable
      source = $("#" + category.sources[0] + "> .source")

      connection = Plumb.getSourceConnection(source)
      if connection
        $scope.confirmRemoveConnection(connection)
      else
        $timeout ->
          DragHelper.showSendReply($('#' + category.sources[0] + ' .source'))
        ,0

  $scope.addAction = (actionset) ->

    if window.dragging or not window.mutable
      return

    $modal.open
      templateUrl: "/partials/node_editor?v=" + version
      controller: NodeEditorController
      resolve:
        options: ->
          nodeType: 'actions'
          actionset: actionset
          action:
            type: if window.ivr then 'say' else 'reply'
            uuid: uuid()

  $scope.moveActionUp = (actionset, action) ->
    Flow.moveActionUp(actionset, action)

  $scope.isMoveable = (action) ->
    return Flow.isMoveableAction(action)

  $scope.confirmRemoveAction = (event, actionset, action) ->

    if window.dragging or not window.mutable
      return

    removeWarning = $(event.target).parent().children('.remove-warning')

    # if our warning is already visible, go ahead and delete
    if removeWarning.is(':visible')
      Flow.removeAction(actionset, action)

    # otherwise warn the user first
    else
      removeWarning.fadeIn()
      $timeout ->
        removeWarning.fadeOut()
      , 1500

    # important not to have coffee do an implicit return here
    # since we are mucking with jquery directly
    return false

  $scope.playRecording = (action_uuid) ->
    $log.debug("Play audio: " + action_uuid)
    $('#' + action_uuid + "_audio").each ->
      audio = $(this)[0]
      if not audio.paused
        audio.pause()
        audio.currentTime = 0

    $('#' + action_uuid + "_audio")[0].play()

  $scope.clickAction = (actionset, action, dragSource=null) ->

    if window.dragging or not window.mutable
      return

    DragHelper.hide()

    # if its the base language, don't show the from text
    if $scope.$parent.flow.base_language and $scope.$parent.flow.base_language != $scope.$parent.language.iso_code

      if action.type in ["send", "reply", "say"]

        fromText = action.msg[$scope.$parent.flow.base_language]

        modalInstance = $modal.open(
          templateUrl: "/partials/translation_modal?v=" + version
          controller: TranslationController
          resolve:
            languages: ->
              from: $scope.$parent.flow.base_language
              to: $scope.$parent.language.iso_code
            translation: ->
              from: fromText
              to: action.msg[$scope.$parent.language.iso_code]
        )

        modalInstance.opened.then ->
          $('textarea').focus()

        modalInstance.result.then (translation) ->
          action = utils.clone(action)
          if translation and translation.strip().length > 0
             action.msg[$scope.$parent.language.iso_code] = translation
          else
            delete action.msg[$scope.$parent.language.iso_code]
          Flow.saveAction(actionset, action)
        , (-> $log.info "Modal dismissed at: " + new Date())

    else

      $modal.open
        templateUrl: "/partials/node_editor?v=" + version
        controller: NodeEditorController
        resolve:
          options: ->
            nodeType: 'actions'
            actionset: actionset
            action: action
            dragSource: dragSource

  $scope.mouseMove = ($event) ->

    # reset our activity interval on any movement
    $rootScope.activityInterval = 5000

    if $rootScope.ghost
      utils.checkCollisions($rootScope.ghost)

      # if we are colliding check if we are also hovering, if so, hide us
      if $rootScope.ghost.hasClass('collision')
        if $("#flow .drop-hover").length > 0
          $rootScope.ghost.hide()
        else
          $rootScope.ghost.show()

      $rootScope.ghost.offset
        left: $event.pageX - ($rootScope.ghost.width() / 2)
        top: $event.pageY
    return false

  # allow use to cancel display of recent messages
  showRecentDelay = null
  $scope.hideRecentMessages = ->

    $timeout.cancel(showRecentDelay)

    if this.category
      this.category._showMessages = false
      this.$parent.ruleset._showMessages = false

    if this.action_set
      this.action_set._showMessages = false

  $scope.showRecentMessages = ->

    hovered = this
    showRecentDelay = $timeout ->

      if hovered.action_set
        action_set = hovered.action_set
        action_set._showMessages = true
        Flow.fetchRecentMessages(action_set.uuid, action_set.destination).then (response) ->
          action_set._messages = response.data

      if hovered.category

        # We are looking at recent messages through a rule
        category = hovered.category
        ruleset = hovered.$parent.ruleset

        # our node and rule should be marked as showing messages
        ruleset._showMessages = true
        category._showMessages = true

        # use all rules as the source so we see all matched messages for the path
        categoryFrom = category.sources.join()
        categoryTo = category.target

        Flow.fetchRecentMessages(ruleset.uuid, categoryTo, categoryFrom).then (response) ->
          category._messages = response.data
    , 500

]

# translating rules
TranslateRulesController = ($scope, $modalInstance, Flow, utils, languages, ruleset) ->

  # clone our ruleset
  ruleset = utils.clone(ruleset)

  for rule in ruleset.rules

    if rule.test.type == "between"
      rule.category = null

    if rule.category
      rule._translation = {category:{}, test:{}}
      rule._translation.category['from'] = rule.category[$scope.$parent.flow.base_language]
      rule._translation.category['to'] = rule.category[$scope.$parent.language.iso_code]

      if typeof(rule.test.test) == "object"
        rule._translation.test['from'] = rule.test.test[$scope.$parent.flow.base_language]
        rule._translation.test['to'] = rule.test.test[$scope.$parent.language.iso_code]

  $scope.ruleset = ruleset
  $scope.languages = languages

  $scope.ok = ->
    for rule in ruleset.rules
      if rule.category
        if rule._translation.category.to and rule._translation.category.to.strip().length > 0
          rule.category[$scope.$parent.language.iso_code] = rule._translation.category.to
        else
          delete rule.category[$scope.$parent.language.iso_code]

        if typeof(rule.test.test) == "object"

          if rule._translation.test.to and rule._translation.test.to.strip().length > 0
            rule.test.test[$scope.$parent.language.iso_code] = rule._translation.test.to
          else
            delete rule.test.test[$scope.$parent.language.iso_code]

    Flow.replaceRuleset(ruleset)
    $modalInstance.close ""

  $scope.cancel = ->
    $modalInstance.dismiss "cancel"

# The controller for our translation modal
TranslationController = ($scope, $modalInstance, languages, translation) ->
  $scope.translation = translation
  $scope.languages = languages

  $scope.ok = (translationText) ->
    $modalInstance.close translationText

  $scope.cancel = ->
    $modalInstance.dismiss "cancel"

# The controller for sub-dialogs when editing rules
RuleOptionsController = ($rootScope, $scope, $modal, $log, $modalInstance, $timeout, utils, ruleset, Flow, Plumb, methods, type) ->

  $scope.ruleset = utils.clone(ruleset)
  $scope.methods = methods
  $scope.type = type

  if $scope.ruleset.webhook_action == null
    $scope.ruleset.webhook_action = 'GET'

  $scope.ok = ->
    ruleset.webhook_action = $scope.ruleset.webhook_action
    ruleset.webhook = $scope.ruleset.webhook
    ruleset.operand = $scope.ruleset.operand
    Flow.markDirty()

    $timeout ->
      Plumb.recalculateOffsets(ruleset.uuid)
    ,0

    $modalInstance.close ""

  $scope.cancel = ->
    $modalInstance.dismiss "cancel"

NodeEditorController = ($rootScope, $scope, $modal, $modalInstance, $timeout, $log, Flow, Plumb, utils, options) ->

  # let our template know our editor type
  $scope.nodeType = options.nodeType
  $scope.ivr = window.ivr
  $scope.options = options

  if options.nodeType == 'rules' or options.nodeType == 'ivr'

    ruleset = options.ruleset

    # our placeholder actions if they flip
    action =
      type: if window.ivr then 'say' else 'reply'
      uuid: uuid()

    actionset =
      _switchedFromRule: true
      x: ruleset.x
      y: ruleset.y
      uuid: uuid()
      actions: [ action ]

  else if options.nodeType == 'actions'

    actionset = options.actionset
    action = options.action

    #our place holder ruleset if the flip
    ruleset =
      _switchedFromAction: true
      x: actionset.x
      y: actionset.y
      uuid: uuid(),
      label: "Response " + ($rootScope.flow.rule_sets.length + 1)
      operand: "@step.value"
      webhook_action: null,
      rules: [
        test:
          test: "true"
          type: "true"
        category: 'All Responses'
        uuid: uuid()
      ]

    # localized category name
    if $rootScope.flow.base_language
      ruleset.rules[0].category = { base:'All Responses' }
      ruleset.rules[0].category[$rootScope.flow.base_language] = 'All Responses'


  $scope.showFlip = ->

    return !$scope.ivr && actionset.actions.length < 2

  #-----------------------------------------------------------------
  # Rule editor
  #-----------------------------------------------------------------

  $scope.ruleset = utils.clone(ruleset)
  $scope.removed = []
  flow = $rootScope.flow

  if not $scope.ruleset.response_type
    if window.ivr
      $scope.ruleset.response_type = 'M'
    else
      $scope.ruleset.response_type = 'C'

  $scope.updateWebhook = () ->

    $modal.open
      templateUrl: "/partials/rule_webhook?v=" + version
      controller: RuleOptionsController
      resolve:
        methods: ->
          ['GET', 'POST']
        type: ->
          'api'
        ruleset: -> $scope.ruleset

  $scope.remove = (rule) ->
    $scope.removed.push(rule)
    index = $scope.ruleset.rules.indexOf(rule)
    $scope.ruleset.rules.splice(index, 1)

  $scope.numericRule =
    test:
      type: 'between'
    config: Flow.getOperatorConfig('between')

  # rules = []
  toRemove = []
  for rule in $scope.ruleset.rules

    if not rule.category
      toRemove.push(rule)
      continue

    # the config is the meta data about our type of operator
    rule.config = Flow.getOperatorConfig(rule.test.type)

    # we need to parse our dates
    if rule.test.type in ['date_before', 'date_after', 'date_equal']
      # relative dates formatted as: @date.today|time_delta:'n'
      # lets rip out the delta parameter and use it as our test instead
      rule.test.base = rule.test.test.slice(24, -1)

    # set the operands
    else if rule.test.type != "between"

      if flow.base_language and rule.test.test and rule.config.localized
        rule.test.base = rule.test.test[flow.base_language]
      else
        rule.test =
          base: rule.test.test

    # and finally the category name
    if flow.base_language
      rule.category.base = rule.category[flow.base_language]
    else
      rule.category =
        base: rule.category

    # find our numeric rule if we have one
    if $scope.ruleset.response_type == 'N' and $scope.ruleset.rules.length > 1
        for rule in $scope.ruleset.rules
          if rule.config.type == 'between'
            $scope.numericRule = rule
            break

  if window.ivr
    # prep our menu
    $scope.numbers = ({ number:num, uuid: uuid() } for num in [1..9])
    $scope.numbers.push
      number: 0
      uuid: uuid()

    for rule in $scope.ruleset.rules

      num = parseInt(rule.test.base)
      if num >= 0 and num <= 9

        # zero comes last on our keypad
        if num == 0
          num = 10
        $scope.numbers[num-1].category = rule.category
        $scope.numbers[num-1].uuid = rule.uuid
        $scope.numbers[num-1].destination = rule.destination

  for rule in toRemove
    $scope.remove(rule)

  $scope.sortableOptions =
    forcePlaceholderSize: true
    scroll:false
    placeholder: "sort-placeholder"

  $scope.updateSplitVariable = ->

    $modal.open
      templateUrl: "/partials/split_variable?v=" + version
      controller: RuleOptionsController
      resolve:
        methods: -> []
        type: -> 'reply'
        ruleset: -> $scope.ruleset

  $scope.clickOpen = ->
    $scope.ruleset.response_type = 'O'

  $scope.clickMultiple = ->
    $scope.ruleset.response_type = 'C'

  $scope.clickNumeric = ->
    $scope.ruleset.response_type = 'N'

  $scope.clickMenu = ->
    $scope.ruleset.response_type = 'M'

  $scope.clickKeypad = ->
    $scope.ruleset.response_type = 'K'
    $scope.ruleset.finished_key = '#'

  $scope.clickRecording = ->
    $scope.ruleset.response_type = 'R'

  $scope.updateCategory = (rule) ->

    # only auto name things if our flag is set
    # we don't want to update categories if they've been set
    if not rule.category._autoName
      return

    categoryName = $scope.getDefaultCategory(rule)

    if rule.category
      rule.category.base = categoryName
    else
      rule.category =
        base: categoryName

  $scope.getDefaultCategory = (rule) ->

    categoryName = ''
    if rule.test and rule.test.base
      categoryName = rule.test.base.strip()

    op = rule.config.type
    if op in ["between"]
      if rule.test.min
        categoryName = rule.test.min

      if rule.test.min and rule.test.max
        categoryName += ' - '

      if rule.test.max
        categoryName += rule.test.max

    else if op == "number"
      categoryName = "numeric"
    else if op == "district"
      categoryName = "district"
    else if op == "state"
      categoryName = "state"
    else if op == "phone"
      categoryName = "phone"
    else if op == "regex"
      categoryName = "matches"
    else if op == "date"
      categoryName = "is a date"
    else if op in ["date_before", "date_equal", "date_after"]
      if categoryName[0] == '-'
        categoryName = "today " + op
      else
        categoryName = "today +" + op

      if categoryName in ['1', '-1']
        categoryName = categoryName + " day"
      else
        categoryName = categoryName + " days"

      if op == 'date_before'
        categoryName = "< " + categoryName
      else if op == 'date_equal'
        categoryName = "= " + categoryName
      else if op == 'date_after'
        categoryName = "> " + categoryName

    # this is a rule matching keywords
    else if op in ["contains", "contains_any", "starts"]
      # take only the first word and title case it.. so "yes y ya" turns into "Yes"
      words = categoryName.trim().split(/\b/)
      if words
        categoryName = words[0].toUpperCase()
        if categoryName.length > 1
          categoryName = categoryName.charAt(0) + categoryName.substr(1).toLowerCase()

    else
      named = $rootScope.opNames[op]
      if named
        categoryName = named + categoryName

    # limit category names to 36 chars
    return categoryName.substr(0, 36)


  stopWatching = $scope.$watch (->$scope.ruleset), ->
    complete = true
    for rule in $scope.ruleset.rules
      if not rule.config.operands == 0
        if not rule.category or not rule.category.base
          complete = false
          break
      else if rule.config.operands == 1
        if not rule.category or not rule.category.base or not rule.test.base
          complete = false
          break
      else if rule.config.operands == 2
        if not rule.category or not rule.category.base or not rule.test.min or not rule.test.min
          complete = false
          break

    if complete
      # we insert this to keep our true rule at the end
      $scope.ruleset.rules.splice $scope.ruleset.rules.length - 1, 0,
        uuid: uuid()
        test:
          type: if window.ivr then "starts" else "contains_any"
        category:
          _autoName: true
          base: ''
        config: if window.ivr then Flow.getOperatorConfig('starts') else Flow.getOperatorConfig('contains_any')
  , true

  $scope.updateRules = ->

    # set the base values on the rule definition
    rules = []

    if $scope.ruleset.response_type == 'M'

      for option in $scope.numbers
        if option.category and option.category.base
          if flow.base_language
            rule =
              uuid: option.uuid
              destination: option.destination
              category: option.category
              test:
                type: 'eq'
                test: option.number

            rule.category[flow.base_language] = option.category.base
          else
            rule =
              uuid: option.uuid
              destination: option.destination
              category: option.category.base
              test:
                type: 'eq'
                test: option.number

          rules.push(rule)

    if $scope.ruleset.response_type == 'C' or $scope.ruleset.response_type == 'K'
      for rule in $scope.ruleset.rules

        # we'll tack our everything rule on the end
        if rule.config.type == "true"
          continue

        # between categories are not required, populate their category name
        if (not rule.category or not rule.category.base) and rule.config.type == 'between' and rule.test.min and rule.test.max
            rule.category =
              base: rule.test.min + " - " + rule.test.max

        # we'll always have an empty rule for new rule creation on the form
        if not rule.category or rule.category.base.strip().length == 0
          continue

        rule.test.type = rule.config.type

        # add our time delta filter for our date operators
        if rule.config.type in ["date_before", "date_after", "date_equal"]
          rule.test.test = "@date.today|time_delta:'" + rule.test.base + "'"
        else
          if flow.base_language and rule.config.localized
            if not rule.test.test
              rule.test.test = {}
            rule.test.test[flow.base_language] = rule.test.base
          else
            rule.test.test = rule.test.base

        if flow.base_language
          rule.category[flow.base_language] = rule.category.base
        else
          rule.category = rule.category.base

        if rule.category
          rules.push(rule)


    else if $scope.ruleset.response_type == 'N'
      rule = $scope.numericRule
      if rule.test.min and rule.test.max
        if flow.base_language
          if not rule.category
            rule.category = {}
          rule.category[flow.base_language] = rule.test.min + " - " + rule.test.max
        else
          rule.category = rule.test.min + " - " + rule.test.max
        rules.push(rule)

    # set the name for our everything rule
    allCategory = "All Responses"
    if rules.length > 0
      allCategory = "Other"

    # grab previous category translations if we have them
    ruleId = uuid()
    destination = null
    for rule in $scope.ruleset.rules
      if rule.config.type == 'true'
        destination = rule.destination
        category = rule.category
        ruleId = rule.uuid
        break

    # if we have a language, add it to our language dict
    if flow.base_language

      # if for some reason we don't have an other rule
      # create an empty category (this really shouldn't happen)
      if not category
        category = {}

      category[flow.base_language] = allCategory
    else
      category = allCategory

    # finally add it to the end of our rule list
    rules.push
      config: Flow.getOperatorConfig("true")
      test:
        test: "true"
        type: "true"
      destination: destination
      uuid: ruleId
      category: category

    $scope.ruleset.rules = rules

  $scope.okRules = ->

    $modalInstance.close ""

    stopWatching()
    $scope.updateRules()

    # unplumb any rules that were explicity removed
    Plumb.disconnectRules($scope.removed)

    # switching from an actionset means removing it and hijacking its connections
    connections = Plumb.getConnectionMap({ target: actionset.uuid })
    if $scope.ruleset._switchedFromAction
      Flow.removeActionSet($scope.actionset)

    # save our new ruleset
    Flow.replaceRuleset($scope.ruleset, false)

    # remove any connections that shouldn't be allowed
    for rule in $scope.ruleset.rules
      if not Flow.isConnectionAllowed(flow, rule.uuid, rule.destination)
        Flow.updateDestination($scope.ruleset.uuid + '_' + rule.uuid, null)

    # steal the old connections if we are replacing an actionset with ourselves
    if $scope.ruleset._switchedFromAction
      $timeout ->
        ruleset_uuid = $scope.ruleset.uuid
        for source of connections
          Flow.updateDestination(source, ruleset_uuid)
      ,0


    # link us up if necessary, we need to do this after our element is created
    if $scope.options.dragSource
      Flow.updateDestination($scope.options.dragSource, $scope.ruleset.uuid)

    # finally, make sure we get saved
    Flow.markDirty()


  $scope.cancel = ->
    stopWatching()
    $modalInstance.dismiss "cancel"

  #-----------------------------------------------------------------
  # Actions editor
  #-----------------------------------------------------------------

  $scope.action = utils.clone(action)
  $scope.actionset = actionset
  $scope.flowId = $scope.$parent.flowId

  # track whether we already have a flow action
  startsFlow = false
  for action in actionset.actions
    if action.type == 'flow' and $scope.action.uuid != action.uuid
      startsFlow = true
      break

  # set up language options
  if $scope.$parent.flow.base_language
    $scope.base_language = $scope.$parent.flow.base_language
    if not $scope.action.lang
      $scope.action.lang = $scope.base_language

  # scope prep for webhook form
  $scope.methods = ['GET', 'POST']
  if not $scope.action.action
    $scope.action.action = 'GET'

  # save to contact prep
  # TODO: this should probably be ajax instead
  $scope.contactFields = $scope.$parent.contactFieldSearch

  for actionConfig in $scope.$parent.actions
    if actionConfig.type == $scope.action.type
      $scope.config = actionConfig

  # a simple function to filter out invalid actions
  $scope.validActionFilter = (action) ->

    if startsFlow and action.type == 'flow'
      return false

    if not window.ivr and (action.type == 'say' or action.type == 'play')
      return false
    if not $scope.$parent.flow.base_language and action.type == 'lang'
      return false
    return true

  $scope.savePlay = ->
    $scope.action.type = 'play'
    Flow.saveAction(actionset, $scope.action)
    $modalInstance.close()

  # Saving a reply SMS in the flow
  $scope.saveMessage = (message, type='reply') ->

    if $scope.base_language
      if typeof($scope.action.msg) != "object"
        $scope.action.msg = {}
      $scope.action.msg[$scope.base_language] = message
    else
      $scope.action.msg = message

    $scope.action.type = type
    Flow.saveAction(actionset, $scope.action)
    $modalInstance.close()

  # Saving an SMS to somebody else
  $scope.saveSend = (omnibox, message) ->
    $scope.action.groups = omnibox.groups
    $scope.action.contacts = omnibox.contacts
    $scope.action.variables = omnibox.variables
    $scope.action.type = 'send'

    if $scope.base_language
      if typeof($scope.action.msg) != "object"
        $scope.action.msg = {}
      $scope.action.msg[$scope.base_language] = message
    else
      $scope.action.msg = message
    Flow.saveAction(actionset, $scope.action)
    $modalInstance.close()

  # Saving labels to add to an SMS
  $scope.saveLabels = (msgLabels) ->

    labels = []
    for msgLabel in msgLabels
      found = false
      for label in $scope.$parent.labels
        if label.id == msgLabel
          found = true
          labels.push
            id: label.id
            name: label.text

      if not found
        labels.push
          id: msgLabel.id
          name: msgLabel.text

    $scope.action.labels = labels


    $scope.action.type = 'add_label'
    Flow.saveAction(actionset, $scope.action)
    $modalInstance.close()


  # Saving the add to or remove from group actions
  $scope.saveGroups = (actionType, omnibox) ->

    $scope.action.type = actionType
    $scope.action.groups = omnibox.groups

    # add our list of variables
    for variable in omnibox.variables
      $scope.action.groups.push(variable.id)

    Flow.saveAction(actionset, $scope.action)
    $modalInstance.close()

  # Save the updating of a contact
  $scope.saveUpdateContact = (field, value) ->

    if field.id.indexOf('[_NEW_]') == 0 and field.text.indexOf("Add new variable:") == 0
      field.text = field.text.slice(18)
      field.id = field.id.slice(7)
      field.id = field.id.toLowerCase().replace(/[^0-9a-z]+/gi, ' ').strip().replace(/[^0-9a-z]+/gi, '_')

    $scope.action.type = 'save'
    $scope.action.field = field.id
    $scope.action.label = field.text
    $scope.action.value = value

    # add the new field to our list so it shows up without reloading
    $scope.$parent.contactFieldSearch.push
      id: field.id
      text: field.text

    Flow.saveAction(actionset, $scope.action)
    $modalInstance.close()

  # save a webhook action
  $scope.saveWebhook = (method, url) ->
    $scope.action.type = 'api'
    $scope.action.action = method
    $scope.action.webhook = url
    Flow.saveAction(actionset, $scope.action)
    $modalInstance.close()

  $scope.saveEmail = (addresses) ->

    to = []
    for address in addresses
      to.push(address.text)
    $scope.action.emails = to
    $scope.action.type = 'email'

    Flow.saveAction(actionset, $scope.action)
    $modalInstance.close()

  # saving flow triggers, both for other people and same contact
  $scope.saveStartFlow = (flow, omnibox) ->

    if omnibox
      $scope.action.type = 'trigger-flow'
      $scope.action.groups = omnibox.groups
      $scope.action.contacts = omnibox.contacts
      $scope.action.variables = omnibox.variables

    else
      $scope.action.type = 'flow'

    flow = flow[0]
    $scope.action.id = flow.id
    $scope.action.name = flow.text
    Flow.saveAction(actionset, $scope.action)
    $modalInstance.close()

  $scope.saveLanguage = () ->

    $scope.action.type = 'lang'

    # lookup the language name for our selection
    for lang in $scope.$parent.languages
      if lang.iso_code == $scope.action.lang
        $scope.action.name = lang.name
        break

    Flow.saveAction(actionset, $scope.action)
    $modalInstance.close()

  $scope.ok = ->
    $timeout ->
      $('.submit').click()
      # link us up if necessary, we need to do this after our element is created
      if options.dragSource
        Flow.updateDestination(options.dragSource, actionset.uuid)
    ,0

    # switching from a ruleset means removing it and hijacking its connections
    if actionset._switchedFromRule
      connections = Plumb.getConnectionMap({ target: $scope.ruleset.uuid })
      Flow.removeRuleset($scope.ruleset)

      $timeout ->
        for source of connections
          # only rules can go to us, actions cant connect to actions
          if source.split('_').length > 1
            Flow.updateDestination(source, actionset.uuid)
          else
            Flow.updateDestination(source, null)
      ,0

  $scope.cancel = ->
    $modalInstance.dismiss "cancel"




SimpleMessageController = ($scope, $modalInstance, $log, title, body, okButton, hideCancel=true) ->
  $scope.title = title
  $scope.body = body
  $scope.okButton = okButton
  $scope.hideCancel = hideCancel

  $scope.ok = ->
    $modalInstance.close "ok"

  $scope.cancel = ->
    $modalInstance.close "cancel"

  return

TerminalWarningController = ($scope, $modalInstance, $log, actionset, flowController) ->

  $scope.title = "End of Flow"
  $scope.body = "You must first add a response to this branch in order to extend it."
  $scope.okButton = "Add Response"

  startsFlow = false
  for action in actionset.actions
    if action.type == 'flow'
      startsFlow = true
      break

  if startsFlow
    $scope.body = "Once another flow is started, this flow can no longer continue. To extend this flow, remove any actions that start another flow."
    $scope.okButton = "Ok"

  $scope.ok = ->
    $modalInstance.close "ok"

    if not startsFlow
      flowController.addAction(actionset)

  $scope.cancel = ->
    $modalInstance.dismiss "cancel"
