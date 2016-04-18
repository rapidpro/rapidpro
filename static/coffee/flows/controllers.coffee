#============================================================================
# Our main controller. Manages our flow state.
#============================================================================
app = angular.module('temba.controllers', ['ui.bootstrap', 'temba.services', 'ngAnimate'])

version = new Date().getTime()

app.controller 'RevisionController', [ '$scope', '$rootScope', '$log', '$timeout', 'Flow', 'Revisions', ($scope, $rootScope, $log, $timeout, Flow, Revisions) ->

  $scope.revisions = ->
    return Revisions.revisions

  # apply the current flow as our definition
  $scope.apply = ->
    $scope.applyDefinition(Flow.flow)

  # go back to our original revision
  $scope.cancel = ->
    if Revisions.original
      $scope.applyDefinition(Revisions.original)
    else
      $scope.hideRevisions()

  # Select the revision show
  $scope.showRevision = (revision) ->

    # show our revision selection
    for other in Revisions.revisions
      other.selected = false
    revision.selected = true

    # store our original definition
    if not Revisions.original
      Revisions.original = Flow.flow

    # show the revision definition
    Revisions.getRevision(revision).then ->
      $scope.showDefinition(Revisions.definition)

  # Show a definition from a revision or our original definition
  $scope.showDefinition = (definition, onChange) ->
    $rootScope.visibleActivity = false
    Flow.flow = null
    jsPlumb.reset()
    $timeout ->
      Flow.flow = definition
      if onChange
        onChange()
    ,0


  # Apply the definition and hide the revision history interface
  $scope.applyDefinition = (definition) ->

    for actionset in definition.action_sets
        for action in actionset.actions
          action.uuid = uuid()

    # remove all revision selection
    for other in Revisions.revisions
      other.selected = false

    markDirty = false
    if definition.metadata.revision != Revisions.original.metadata.revision
      definition.metadata.saved_on = Revisions.original.metadata.saved_on
      markDirty = true

    $scope.showDefinition definition, ->
      $scope.hideRevisions()
      # save if things have changed
      if markDirty
        Flow.markDirty()

  $scope.hideRevisions = ->
    Revisions.original = null
    $rootScope.visibleActivity = true
    $rootScope.showRevisions = false
]

app.controller 'FlowController', [ '$scope', '$rootScope', '$timeout', '$log', '$interval', '$upload', 'Flow', 'Plumb', 'DragHelper', 'utils', ($scope, $rootScope, $timeout, $log, $interval, $upload, Flow, Plumb, DragHelper, utils) ->

  # inject into our gear menu
  $rootScope.gearLinks = []
  $rootScope.ivr = window.ivr

  $scope.getContactFieldName = (ruleset) ->
    if not ruleset._contactFieldName
      ruleset._contactFieldName = Flow.getContactField(ruleset)
    return ruleset._contactFieldName

  $scope.getFlowFieldName = (ruleset) ->
    if not ruleset._flowFieldName
      ruleset._flowFieldName = Flow.getFlowField(ruleset)
    return ruleset._flowFieldName

  # when they click on an injected gear item
  $scope.clickGearMenuItem = (id) ->

    # setting our default language
    if id == 'default_language'
      modal = new ConfirmationModal(gettext('Default Language'), gettext('The default language for the flow is used for contacts which have no preferred language. Are you sure you want to set the default language for this flow to') + ' <span class="attn">' + Flow.language.name + "</span>?")
      modal.addClass('warning')
      modal.setListeners
        onPrimary: ->
          $scope.setBaseLanguage(Flow.language)
      modal.show()

    return false


  $rootScope.activityInterval = 5000

  # fetch our flow to get started
  $scope.init = ->
    Flow.fetch window.flowId, ->
      $scope.updateActivity()
      $scope.flow = Flow.flow

  showDialog = (title, body, okButton='Okay', hideCancel=true) ->
    resolveObj =
      title: -> title
      body: -> body
      okButton: -> okButton
      hideCancel: -> hideCancel

    $scope.dialog = utils.openModal("/partials/modal", SimpleMessageController, resolveObj)
    return $scope.dialog

  $scope.showRevisionHistory = ->
    $scope.$evalAsync ->
      $rootScope.showRevisions = true

  $scope.setBaseLanguage = (lang) ->

    # now we have a real base language, remove the default placeholder
    if Flow.languages[0].name == gettext('Default')
      Flow.languages.splice(0, 1)

    # reorder our languages so the base language is first
    Flow.languages.splice(Flow.languages.indexOf(lang), 1)
    Flow.languages.unshift(lang)

    # set the base language
    Flow.flow.base_language = lang.iso_code

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
      if not action.recording
        action.recording = {}
      action.recording[Flow.language.iso_code] = data['path']

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
    Flow.language = lang
    $scope.language = lang
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


  $scope.$watch (->Flow.flow), (current) ->

    $scope.flow = Flow.flow
    $scope.languages = Flow.languages
    $scope.language = Flow.language

    if current
      jsPlumb.bind('connectionDrag', (connection) -> $scope.onConnectorDrag(connection))
      jsPlumb.bind('connectionDragStop', (connection) -> $scope.onConnectorDrop(connection))
      jsPlumb.bind('beforeDrop', (sourceId, targetId) -> $scope.onBeforeConnectorDrop(sourceId, targetId))
    else
      jsPlumb.unbind('connectionDrag')
      jsPlumb.unbind('connectionDragStop')

  $scope.$watch (->$rootScope.visibleActivity), ->
    if $rootScope.visibleActivity
      if Flow.flow
        for node in Flow.flow.rule_sets.concat Flow.flow.action_sets
          Flow.applyActivity(node, $rootScope.visibleActivity)
    return

  # our categories can be combined rules
  # we always defer to the first one, this is used for plumbing
  $scope.getSource = (category) ->
    return category.sources[0]

  $scope.onBeforeConnectorDrop = (props) ->

    errorMessage = Flow.getConnectionError(props.sourceId, props.targetId)
    if errorMessage
      $rootScope.ghost.hide()
      $rootScope.ghost = null
      showDialog('Invalid Connection', errorMessage)
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

          msg = {}
          msg[Flow.flow.base_language] = ''

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

          category = {}
          category[Flow.flow.base_language] = "All Responses"

          ruleset =
            x: ghost[0].offsetLeft
            y: ghost[0].offsetTop
            uuid: targetId,
            label: "Response " + (Flow.flow.rule_sets.length + 1)
            operand: "@step.value"
            webhook_action: null,
            ruleset_type: if window.ivr then 'wait_digit' else 'wait_message',
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

    msg = {}
    msg[Flow.flow.base_language] = ''

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
    if actionset._lastActionMissingTranslation == null
      lastAction = actionset.actions[actionset.actions.length - 1]
      actionset._lastActionMissingTranslation = false
      if Flow.language
        if Flow.language.iso_code != Flow.flow.base_language
          if lastAction.msg and lastAction.type in ['reply', 'send', 'send', 'say'] and not lastAction.msg[Flow.language.iso_code]
              actionset._lastActionMissingTranslation = true
    return actionset._lastActionMissingTranslation


  $scope.broadcastToStep = (uuid) ->
    window.broadcastToNode(uuid)

  $scope.addNote = (event) ->
    Flow.addNote(event.offsetX, event.offsetY)

  $scope.removeNote = (note) ->
    Flow.removeNote(note)

  $scope.clickRuleset = (ruleset, dragSource=null) ->
    if window.dragging or not window.mutable
      return

    DragHelper.hide()

    if Flow.language and Flow.flow.base_language != Flow.language.iso_code
      resolveObj =
        languages: ->
          from: Flow.flow.base_language
          to: Flow.language.iso_code
        ruleset: -> ruleset

      $scope.dialog = utils.openModal("/partials/translate_rules", TranslateRulesController, resolveObj)

    else

      if window.ivr
        resolveObj =
          options: ->
            nodeType: 'ivr'
            ruleset: ruleset
            dragSource: dragSource
          scope: $scope
        $scope.dialog = utils.openModal("/partials/node_editor", NodeEditorController, resolveObj)

      else
        resolveObj =
          options: ->
            nodeType: 'rules'
            ruleset: ruleset
            dragSource: dragSource

        $scope.dialog = utils.openModal("/partials/node_editor", NodeEditorController, resolveObj)

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
      resolveObj =
        actionset: -> actionset
        flowController: -> $scope
      $scope.dialog = utils.openModal("/partials/modal", TerminalWarningController, resolveObj)
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
    resolveObj =
      options: ->
        nodeType: 'actions'
        actionset: actionset
        action:
          type: if window.ivr then 'say' else 'reply'
          uuid: uuid()

    $scope.dialog = utils.openModal("/partials/node_editor", NodeEditorController, resolveObj)

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
    if Flow.language and Flow.flow.base_language != Flow.language.iso_code

      if action.type in ["send", "reply", "say"]

        fromText = action.msg[Flow.flow.base_language]

        resolveObj =
          languages: ->
            from: Flow.flow.base_language
            to: Flow.language.iso_code
          translation: ->
            from: fromText
            to: action.msg[Flow.language.iso_code]

        $scope.dialog = utils.openModal("/partials/translation_modal", TranslationController, resolveObj)

        $scope.dialog.opened.then ->
          $('textarea').focus()

        $scope.dialog.result.then (translation) ->
          action = utils.clone(action)
          if translation and translation.strip().length > 0
             action.msg[Flow.language.iso_code] = translation
          else
            delete action.msg[Flow.language.iso_code]
          Flow.saveAction(actionset, action)
        , (-> $log.info "Modal dismissed at: " + new Date())

    else
      resolveObj =
        options: ->
          nodeType: 'actions'
          actionset: actionset
          action: action
          dragSource: dragSource

      $scope.dialog = utils.openModal("/partials/node_editor", NodeEditorController, resolveObj)

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
      rule._translation.category['from'] = rule.category[Flow.flow.base_language]
      rule._translation.category['to'] = rule.category[Flow.language.iso_code]

      if typeof(rule.test.test) == "object"
        rule._translation.test['from'] = rule.test.test[Flow.flow.base_language]
        rule._translation.test['to'] = rule.test.test[Flow.language.iso_code]

  $scope.ruleset = ruleset
  $scope.languages = languages
  $scope.language = Flow.language

  $scope.ok = ->

    for rule in ruleset.rules
      if rule.category
        if rule._translation.category.to and rule._translation.category.to.strip().length > 0
          rule.category[Flow.language.iso_code] = rule._translation.category.to
        else
          delete rule.category[Flow.language.iso_code]

        if typeof(rule.test.test) == "object"

          if rule._translation.test.to and rule._translation.test.to.strip().length > 0
            rule.test.test[Flow.language.iso_code] = rule._translation.test.to
          else
            delete rule.test.test[Flow.language.iso_code]

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
RuleOptionsController = ($rootScope, $scope, $log, $modalInstance, $timeout, utils, ruleset, Flow, Plumb, methods, type) ->

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

NodeEditorController = ($rootScope, $scope, $modalInstance, $timeout, $log, Flow, Plumb, utils, options) ->

  # let our template know our editor type
  $scope.nodeType = options.nodeType
  $scope.ivr = window.ivr
  $scope.options = options

  $scope.contactFields = Flow.contactFieldSearch
  $scope.updateContactFields = Flow.updateContactSearch

  $scope.actionConfigs = Flow.actions
  $scope.rulesetConfigs = Flow.rulesets
  $scope.operatorConfigs = Flow.operators

  # all org languages except default
  $scope.languages = utils.clone(Flow.languages).filter (lang) -> lang.name isnt "Default"

  formData = {}

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

    # if its a language type, see if we need to add a missing lanugage to the list
    if action.type == "lang"
      found = false
      for lang in $scope.languages
        if lang.iso_code == action.lang
          found = true
          break

      if not found
        $scope.languages.push({name:action.name, iso_code:action.lang})
        $scope.languages.sort (a, b) ->
          if a.name < b.name
            return -1
          if a.name > b.name
            return 1
          return 0

    #our place holder ruleset if the flip
    ruleset =
      _switchedFromAction: true
      x: actionset.x
      y: actionset.y
      uuid: uuid(),
      label: "Response " + (Flow.flow.rule_sets.length + 1)
      operand: "@step.value"
      webhook_action: null,
      ruleset_type: if window.ivr then 'wait_digit' else 'wait_message',
      rules: [
        test:
          test: "true"
          type: "true"
        category: 'All Responses'
        uuid: uuid()
      ]

    # localized category name
    ruleset.rules[0].category = { _base:'All Responses' }
    ruleset.rules[0].category[Flow.flow.base_language] = 'All Responses'

  formData.rulesetConfig = Flow.getRulesetConfig({type:ruleset.ruleset_type})

  $scope.updateActionForm = (config) ->

    # emails are not localized, if our msg is localized, grab the base text
    if config.type == 'email'
      if typeof $scope.action.msg == 'object'
        if Flow.flow.base_language of $scope.action.msg
          $scope.action.msg = $scope.action.msg[Flow.flow.base_language]
        else
          $scope.action.msg = ''

  $scope.showFlip = ->
    return actionset.actions.length < 2

  #-----------------------------------------------------------------
  # Rule editor
  #-----------------------------------------------------------------

  $scope.ruleset = utils.clone(ruleset)
  $scope.removed = []
  flow = Flow.flow
  $scope.flowFields = Flow.getFlowFields(ruleset)
  $scope.fieldIndexOptions = [{text:'first', id: 0},
                              {text:'second', id: 1},
                              {text:'third', id: 2},
                              {text:'fourth', id: 3},
                              {text:'fifth', id: 4},
                              {text:'sixth', id: 5},
                              {text:'seventh', id: 6},
                              {text:'eighth', id: 7},
                              {text:'ninth', id: 8}]

  $scope.fieldDelimiterOptions = [{text:'space', id: ' '},
                                  {text:'plus', id: '+'},
                                  {text:'period', id: '.'}]

  formData.flowField = Flow.getFieldSelection($scope.flowFields, $scope.ruleset.operand, true)
  formData.contactField = Flow.getFieldSelection($scope.contactFields, $scope.ruleset.operand, false)

  config = $scope.ruleset.config
  if not config
    config = {}

  formData.fieldIndex = Flow.getFieldSelection($scope.fieldIndexOptions, config.field_index, true)
  formData.fieldDelimiter = Flow.getFieldSelection($scope.fieldDelimiterOptions, config.field_delimiter, true)

  # default webhook action
  if not $scope.ruleset.webhook_action
    $scope.ruleset.webhook_action = 'GET'

  $scope.hasRules = () ->
    if $scope.formData.rulesetConfig
      return $scope.formData.rulesetConfig.type in Flow.supportsRules

  $scope.updateWebhook = () ->
    resolveObj =
      methods: ->
        ['GET', 'POST']
      type: ->
        'api'
      ruleset: -> $scope.ruleset

    utils.openModal("/partials/rule_webhook", RuleOptionsController, resolveObj)

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
    rule._config = Flow.getOperatorConfig(rule.test.type)

    # we need to parse our dates
    if rule.test.type in ['date_before', 'date_after', 'date_equal']
      # relative dates formatted as: @(date.today + n)
      # lets rip out the delta parameter and use it as our test instead
      rule.test._base = rule.test.test.slice(15, -1)

    # set the operands
    else if rule.test.type != "between"

      if rule.test.test and rule._config.localized
        rule.test._base = rule.test.test[flow.base_language]
      else
        rule.test =
          _base: rule.test.test

    # and finally the category name
    rule.category._base = rule.category[flow.base_language]

  if window.ivr
    # prep our menu
    $scope.numbers = ({ number:num, uuid: uuid() } for num in [1..9])
    $scope.numbers.push
      number: 0
      uuid: uuid()

    for rule in $scope.ruleset.rules

      num = parseInt(rule.test._base)
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
    resolveObj =
      methods: -> []
      type: -> 'reply'
      ruleset: -> $scope.ruleset

    utils.openModal("/partials/split_variable", RuleOptionsController, resolveObj)

  $scope.updateCategory = (rule) ->

    # only auto name things if our flag is set
    # we don't want to update categories if they've been set
    if not rule.category._autoName
      return

    categoryName = $scope.getDefaultCategory(rule)

    if rule.category
      rule.category._base = categoryName
    else
      rule.category =
        _base: categoryName

  $scope.isVisibleOperator = (operator) ->
    if $scope.formData.rulesetConfig.type == 'wait_digits'
      if not operator.voice
        return false

    if operator.type == "true"
      return false

    return true

  $scope.isVisibleRulesetType = (rulesetConfig) ->
    valid = flow.flow_type in rulesetConfig.filter

    if (rulesetConfig.type == 'flow_field' or rulesetConfig.type == 'form_field') and $scope.flowFields.length == 0
      return false

    if rulesetConfig.type == 'contact_field' and $scope.contactFields.length == 0
      return false

    return valid

  $scope.getDefaultCategory = (rule) ->

    categoryName = ''
    if rule.test and rule.test._base
      categoryName = rule.test._base.strip()

    op = rule._config.type
    if op in ["between"]
      if rule.test.min
        categoryName = rule.test.min

      if rule.test.min and rule.test.max
        categoryName += ' - '

      if rule.test.max
        categoryName += rule.test.max

    else if op == "number"
      categoryName = "numeric"
    else if op == "ward"
      categoryName = "ward"
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
      days = rule.test._base
      if days
        if days[0] == '-'
          categoryName = "today " + days
        else
          categoryName = "today +" + days

        if days in ['1', '-1']
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
      named = Flow.opNames[op]
      if named
        categoryName = named + categoryName

    # limit category names to 36 chars
    return categoryName.substr(0, 36)


  stopWatching = $scope.$watch (->$scope.ruleset), ->
    complete = true
    for rule in $scope.ruleset.rules
      if not rule._config.operands == 0
        if not rule.category or not rule.category._base
          complete = false
          break
      else if rule._config.operands == 1
        if not rule.category or not rule.category._base or not rule.test._base
          complete = false
          break
      else if rule._config.operands == 2
        if not rule.category or not rule.category._base or not rule.test.min or not rule.test.min
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
          _base: ''
        _config: if window.ivr then Flow.getOperatorConfig('starts') else Flow.getOperatorConfig('contains_any')
  , true

  $scope.updateRules = (ruleset, rulesetConfig) ->

    # start with an empty list of rules
    rules = []

    # create rules off of an IVR menu configuration
    if ruleset.ruleset_type == 'wait_digit'
      for option in $scope.numbers
        if option.category and option.category._base
          rule =
            uuid: option.uuid
            destination: option.destination
            category: option.category
            test:
              type: 'eq'
              test: option.number
          rule.category[flow.base_language] = option.category._base

          rules.push(rule)

    # rules configured from our select widgets
    if $scope.hasRules()

      for rule in ruleset.rules
        # we'll tack our everything rule on the end
        if rule._config.type == "true"
          continue

        # between categories are not required, populate their category name
        if (not rule.category or not rule.category._base) and rule._config.type == 'between' and rule.test.min and rule.test.max
            rule.category =
              _base: rule.test.min + " - " + rule.test.max

        # we'll always have an empty rule for new rule creation on the form
        if not rule.category or (rule.category._base.strip().length == 0)
          continue

        rule.test.type = rule._config.type

        # add our time delta filter for our date operators
        if rule._config.type in ["date_before", "date_after", "date_equal"]
          rule.test.test = "@(date.today + " + rule.test._base + ")"
        else
          if rule._config.localized
            if not rule.test.test
              rule.test.test = {}
            rule.test.test[flow.base_language] = rule.test._base
          else
            rule.test.test = rule.test._base

        rule.category[flow.base_language] = rule.category._base
        if rule.category
          rules.push(rule)

    # set the name for our everything rule
    allCategory = "All Responses"
    if rules.length > 0
      allCategory = "Other"

    # grab previous category translations if we have them
    ruleId = uuid()
    destination = null
    for rule in ruleset.rules
      if rule._config.type == 'true'
        destination = rule.destination
        category = rule.category
        ruleId = rule.uuid
        break

    # if for some reason we don't have an other rule
    # create an empty category (this really shouldn't happen)
    if not category
      category = {}

    category[flow.base_language] = allCategory

    # finally add it to the end of our rule list
    rules.push
      _config: Flow.getOperatorConfig("true")
      test:
        test: "true"
        type: "true"
      destination: destination
      uuid: ruleId
      category: category

    $scope.ruleset.rules = rules

  $scope.okRules = ->

    # close our dialog
    stopWatching()
    $modalInstance.close ""

    $timeout ->
      # changes from the user
      ruleset = $scope.ruleset
      rulesetConfig = $scope.formData.rulesetConfig
      contactField = $scope.formData.contactField
      flowField = $scope.formData.flowField

      # save whatever ruleset type they are setting us to
      ruleset.ruleset_type = rulesetConfig.type

      # settings for a message form
      if rulesetConfig.type == 'form_field'
        ruleset.operand = '@flow.' + flowField.id
        ruleset.config =
          field_index: $scope.formData.fieldIndex.id
          field_delimiter: $scope.formData.fieldDelimiter.id

      # update our operand if they selected a contact field explicitly
      else if ruleset.ruleset_type == 'contact_field'
        ruleset.operand = '@contact.' + contactField.id

      # or if they picked a flow field
      else if ruleset.ruleset_type == 'flow_field'
        ruleset.operand = '@flow.' + flowField.id

      # or just want to evaluate against a message
      else if ruleset.ruleset_type == 'wait_message'
        ruleset.operand = '@step.value'

      # clear our webhook if we aren't the right type
      # TODO: this should live in a json config blob
      if ruleset.ruleset_type != 'webhook'
        ruleset.webhook = null
        ruleset.webhook_action = null

      # update our rules accordingly
      $scope.updateRules(ruleset, rulesetConfig)

      # unplumb any rules that were explicity removed
      Plumb.disconnectRules($scope.removed)

      # switching from an actionset means removing it and hijacking its connections
      connections = Plumb.getConnectionMap({ target: actionset.uuid })
      if ruleset._switchedFromAction
        Flow.removeActionSet($scope.actionset)

      # save our new ruleset
      Flow.replaceRuleset(ruleset, false)

      # remove any connections that shouldn't be allowed
      for rule in ruleset.rules
        if rule.destination and not Flow.isConnectionAllowed(ruleset.uuid + '_' + rule.uuid, rule.destination)
          Flow.updateDestination($scope.ruleset.uuid + '_' + rule.uuid, null)

      # steal the old connections if we are replacing an actionset with ourselves
      if ruleset._switchedFromAction
        $timeout ->
          ruleset_uuid = ruleset.uuid
          for source of connections
            if Flow.isConnectionAllowed(source, ruleset_uuid)
              Flow.updateDestination(source, ruleset_uuid)
        ,0

      # link us up if necessary, we need to do this after our element is created
      if $scope.options.dragSource
        if Flow.isConnectionAllowed($scope.options.dragSource, ruleset.uuid)
          Flow.updateDestination($scope.options.dragSource, ruleset.uuid)

      # finally, make sure we get saved
      Flow.markDirty()
    ,0

  $scope.cancel = ->
    stopWatching()
    $modalInstance.dismiss "cancel"

  #-----------------------------------------------------------------
  # Actions editor
  #-----------------------------------------------------------------

  $scope.action = utils.clone(action)
  $scope.actionset = actionset
  $scope.flowId = window.flowId

  # track whether we already have a flow action
  startsFlow = false
  for action in actionset.actions
    if action.type == 'flow' and $scope.action.uuid != action.uuid
      startsFlow = true
      break

  # set up language options
  $scope.base_language = Flow.flow.base_language
  if not $scope.action.lang
    $scope.action.lang = Flow.base_language

  # scope prep for webhook form
  $scope.methods = ['GET', 'POST']
  if not $scope.action.action
    $scope.action.action = 'GET'

  $scope.config = Flow.getActionConfig({type:$scope.action.type})

  # a simple function to filter out invalid actions
  $scope.validActionFilter = (action) ->

    valid = false
    if action.filter
      valid = flow.flow_type in action.filter

    if startsFlow and action.type == 'flow'
      return false

    # TODO: if the org doesn't have lanugaes filter out lang
    # if not Flow.flow.base_language and action.type == 'lang'
    #  return false

    return valid

  $scope.savePlay = ->
    $scope.action.type = 'play'
    Flow.saveAction(actionset, $scope.action)
    $modalInstance.close()

  # Saving a reply SMS in the flow
  $scope.saveMessage = (message, type='reply') ->

    if typeof($scope.action.msg) != "object"
      $scope.action.msg = {}
    $scope.action.msg[$scope.base_language] = message

    $scope.action.type = type
    Flow.saveAction(actionset, $scope.action)
    $modalInstance.close()

  # Saving an SMS to somebody else
  $scope.saveSend = (omnibox, message) ->
    $scope.action.groups = omnibox.groups
    $scope.action.contacts = omnibox.contacts
    $scope.action.variables = omnibox.variables
    $scope.action.type = 'send'

    if typeof($scope.action.msg) != "object"
      $scope.action.msg = {}
    $scope.action.msg[$scope.base_language] = message

    Flow.saveAction(actionset, $scope.action)
    $modalInstance.close()

  # Saving labels to add to an SMS
  $scope.saveLabels = (msgLabels) ->

    labels = []
    for msgLabel in msgLabels
      found = false
      for label in Flow.labels
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

      # add the new field to our list so it shows up without reloading
      Flow.contactFieldSearch.push
        id: field.id
        text: field.text

      Flow.updateContactSearch.push
        id: field.id
        text: field.text


    $scope.action.type = 'save'
    $scope.action.field = field.id
    $scope.action.label = field.text
    $scope.action.value = value


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
    for lang in Flow.languages
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

  $scope.formData = formData


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
