#============================================================================
# Our main controller. Manages our flow state.
#============================================================================
app = angular.module('temba.controllers', ['ui.bootstrap', 'temba.services', 'ngAnimate'])

version = new Date().getTime()

defaultRuleSetType = ->
  if window.ivr
    'wait_digit'
  else if window.ussd
    'wait_menu'
  else
    'wait_message'

defaultActionSetType = ->
  if window.ivr
    'say'
  else if window.ussd
    'end_ussd'
  else
    'reply'

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
        if not action.uuid
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
  $rootScope.ussd = window.ussd
  $rootScope.hasAirtimeService = window.hasAirtimeService

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

  showConnectTransferTo = ->
    modal = new ConfirmationModal(gettext("TransferTo Disconnected"), gettext("No TransferTo account connected. Please first connect your TransferTo account."))
    modal.addClass('airtime-warning')
    modal.setPrimaryButton(gettext("Connect TransferTo Account"))
    modal.setListeners
      onPrimary: ->
        document.location.href = window.connectAirtimeServiceURL
    modal.show()
    return

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
  $scope.onFileSelect = ($files, actionset, action, save=true) ->

    if window.dragging or not window.mutable
      return

    scope = @
    # enforce just one file
    if $files.length > 1
      showDialog("Too Many Files", "To upload a file, please drag and drop one file for each step.")
      return

    file = $files[0]
    if not file.type
      return

    # check for valid voice prompts
    if action.type == 'say' and file.type != 'audio/wav' and file.type != 'audio/x-wav'
      showDialog('Invalid Format', 'Voice prompts must in wav format. Please choose a wav file and try again.')
      return

    parts = file.type.split('/')
    media_type = parts[0]
    media_encoding = parts[1]

    if action.type in ['reply', 'send']
      if media_type not in ['audio', 'video', 'image']
        showDialog('Invalid Attachment', 'Attachments must be either video, audio, or an image.')
        return

      if media_type == 'audio' and media_encoding != 'mp3'
        showDialog('Invalid Format', 'Audio attachments must be encoded as mp3 files.')
        return

    if action.type in ['reply', 'send'] and (file.size > 20000000 or (file.name.endsWith('.jpg') and file.size > 500000))
      showDialog('File Size Exceeded', "The file size should be less than 500kB for images and less than 20MB for audio and video files. Please choose another file and try again.")
      return

    # if we have a recording already, confirm they want to replace it
    if action.type == 'say' and action._translation_recording
      modal = showDialog('Overwrite Recording', 'This step already has a recording, would you like to replace this recording with ' + file.name + '?', 'Overwrite Recording', false)
      modal.result.then (value) ->
        if value == 'ok'
          action._translation_recording = null
          scope.onFileSelect($files, actionset, action)
      return

    # if we have an attachment already, confirm they want to replace it
    if action.type in ['reply', 'send'] and action._media
      modal = showDialog('Overwrite Attachment', 'This step already has an attachment, would you like to replace this attachment with ' + file.name + '?', 'Overwrite Attachemnt', false)
      modal.result.then (value) ->
        if value == 'ok'
          action._media = null
          action._attachURL = null
          action._attachType = null
          scope.onFileSelect($files, actionset, action)
      return


    action.uploading = true

    uploadURL = null
    if action.type == 'say'
      uploadURL = window.uploadURL

    if action.type in ['reply', 'send']
      uploadURL = window.uploadMediaURL

    if not uploadURL
      return

    $scope.upload = $upload.upload
      url: uploadURL
      data:
        actionset: actionset.uuid
        action:  if action.uuid? then action.uuid else ''
      file: file
    .progress (evt) ->
      $log.debug("percent: " + parseInt(100.0 * evt.loaded / evt.total))
      return
    .success (data, status, headers, config) ->
      if action.type == 'say'
        if not action.recording
          action.recording = {}
        action.recording[Flow.language.iso_code] = data['path']

      if action.type in ['reply', 'send']
        if not action.media
          action.media = {}
        action.media[Flow.language.iso_code] = file.type + ':' + data['path']

      # make sure our translation state is updated
      action.uploading = false
      action.dirty = true

      if action.media and action.media[Flow.language.iso_code]
        parts = action.media[Flow.language.iso_code].split(/:(.+)/)
        if parts.length >= 2
          action._media =
            mime: parts[0]
            url:  window.mediaURL + parts[1]
            type: parts[0].split('/')[0]
      if save
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
      return

    $.ajax(
      type: "GET"
      url: activityURL
      cache: false
      success: (data, status, xhr) ->

        $rootScope.is_starting = data.is_starting

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

    if not $rootScope.ghost and connection.targetId == connection.suspendedElementId
      return false

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
            exit_uuid: uuid()
            actions: [
              type: defaultActionSetType()
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
            ruleset_type: defaultRuleSetType(),
            rules: [
              test:
                test: "true"
                type: "true"
              category: category
              uuid: uuid()
            ]

          $scope.clickRuleset(ruleset, connection.sourceId)
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

    scope = if $rootScope.ussd then 'rules' else jsPlumb.getSourceScope(connection.sourceId)
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
      uuid: uuid(),
      exit_uuid: uuid(),
      actions: [
        uuid: uuid()
        type: defaultActionSetType()
        msg: msg
      ]

    @clickAction(actionset, actionset.actions[0])

  $scope.createFirstUssd = ->

    category = {}
    category[Flow.flow.base_language] = "All Responses"

    ruleset =
      x: 100
      y: 0
      uuid: uuid()
      label: "Response " + (Flow.flow.rule_sets.length + 1)
      webhook_action: null
      ruleset_type: defaultRuleSetType()
      rules: [
        test:
          test: "true"
          type: "true"
        category: category
        uuid: uuid()
      ]
      config: {}

    @clickRuleset(ruleset)

  # filter for translation menu
  $scope.notBaseLanguageFilter = (lang) ->
    return lang.iso_code != $scope.flow.base_language

  $scope.translatableRuleFilter = (rule) ->
    return rule.type == 'contains_any'

  # method to determine if the last action in an action set is missing a translation
  # this is necessary to style the bottom of the action set node container accordingly
  $scope.lastActionMissingTranslation = (actionset) ->
      lastAction = actionset.actions[actionset.actions.length - 1]
      if lastAction
        return lastAction._missingTranslation

  $scope.broadcastToStep = (event, uuid, count) ->
    window.broadcastToNode(uuid, count)
    event.stopPropagation()

  $scope.addNote = (event) ->
    Flow.addNote(event.offsetX, event.offsetY)

  $scope.removeNote = (note) ->
    Flow.removeNote(note)

  $scope.clickRuleset = (ruleset, dragSource=null) ->

    if window.dragging or not window.mutable
      return

    DragHelper.hide()

    # show message asking to connect TransferTo account for existing airtime node
    if ruleset.ruleset_type == 'airtime' and not $rootScope.hasAirtimeService
      showConnectTransferTo()
      return

    if Flow.language and Flow.flow.base_language != Flow.language.iso_code and not dragSource
      resolveObj =
        languages: ->
          from: Flow.flow.base_language
          to: Flow.language.iso_code
        ruleset: -> ruleset
        translation: ->
          {}

      # USSD ruleset needs more translation
      if Flow.flow.flow_type == 'U'
        resolveObj.translation = ->
          from: ruleset.config.ussd_message[Flow.flow.base_language]
          to: ruleset.config.ussd_message[Flow.language.iso_code]

      $scope.dialog = utils.openModal("/partials/translate_rules", TranslateRulesController, resolveObj)

    else
      if window.ivr
        resolveObj =
          options: ->
            nodeType: 'ivr'
            ruleset: ruleset
            dragSource: dragSource
          scope: $scope
          flowController: -> $scope

        $scope.dialog = utils.openModal("/partials/node_editor", NodeEditorController, resolveObj)

      else
        resolveObj =
          options: ->
            nodeType: 'rules'
            ruleset: ruleset
            dragSource: dragSource
          flowController: -> $scope

        $scope.dialog = utils.openModal("/partials/node_editor", NodeEditorController, resolveObj)

  $scope.confirmRemoveWebhook = (event, ruleset) ->

    if window.dragging or not window.mutable
      return

    removeWarning = $(event.target).parent().children('.remove-warning')

    # if our warning is already visible, go ahead and delete
    if removeWarning.is(':visible')
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
      Flow.removeRuleset(ruleset.uuid)

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
          type: defaultActionSetType()
          uuid: uuid()

      flowController: -> $scope

    $scope.dialog = utils.openModal("/partials/node_editor", NodeEditorController, resolveObj)

  $scope.moveActionUp = (actionset, action) ->
    Flow.moveActionUp(actionset, action)

  $scope.isMoveable = (action) ->
    return Flow.isMoveableAction(action)

  $scope.hasEndUssd = (actionset) ->
    actionset.actions?.some (action) ->
      action.type == "end_ussd"

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

      if action.type in ["send", "reply", "say", "end_ussd"]

        translations = [
          {
            name: 'Message Text',
            from: action.msg[Flow.flow.base_language],
            to: action.msg[Flow.language.iso_code],
            fromQuickReplies: action.quick_replies || []
          }
        ]

        # add in our media for localization if we have some
        if action.media and action.media[Flow.flow.base_language]

          fromMedia = action.media[Flow.flow.base_language]
          toMedia = action.media[Flow.language.iso_code]

          # this is a bit of a hack, our localizable strings take the form of audio:...
          # but uploads are in the form of audio/wav:http...
          fromMediaSplit = fromMedia?.split(':')
          toMediaSplit = toMedia?.split(':')
          mimeType = fromMediaSplit?[0].split('/')

          # we only care about types that aren't full mime types
          if mimeType?.length == 1
            translations.push({ name:'Attachment', type: mimeType, from:fromMediaSplit[1], to:toMediaSplit?[1], input:true})

        resolveObj =
          language: ->
            from: Flow.flow.base_language
            to: Flow.language.iso_code
            name: Flow.language.name
          translations: -> translations

        $scope.dialog = utils.openModal("/partials/translation_modal", TranslationController, resolveObj)

        $scope.dialog.opened.then ->
          $('textarea').focus()

        $scope.dialog.result.then (translations) ->
          action = utils.clone(action)

          for translation in translations
            results = action.msg
            translated = if translation.to?.strip().length > 0 then translation.to else null

            if translation.fromQuickReplies? && translation.fromQuickReplies != []
              action.quick_replies = translation.fromQuickReplies
            
            if translation.name == "Attachment"
              results = action.media
              if translated
                translated = translation.type + ':' + translated

            if translated
              results[Flow.language.iso_code] = translated
            else
              delete results[Flow.language.iso_code]

          Flow.saveAction(actionset, action)
        , (-> $log.info "Modal dismissed at: " + new Date())

    else
      resolveObj =
        options: ->
          nodeType: 'actions'
          actionset: actionset
          action: action
          dragSource: dragSource
        flowController: -> $scope

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

        Flow.fetchRecentMessages([action_set.exit_uuid], action_set.destination).then (response) ->
          action_set._messages = response.data

      if hovered.category

        # We are looking at recent messages through a rule
        category = hovered.category
        ruleset = hovered.$parent.ruleset

        # our node and rule should be marked as showing messages
        ruleset._showMessages = true
        category._showMessages = true

        # get all recent messages for all rules that make up this category
        Flow.fetchRecentMessages(category.sources, category.target).then (response) ->
          category._messages = response.data
    , 500

  $scope.clickShowActionMedia = ->
    clicked = this
    action = clicked.action
    resolveObj =
      action: -> action
      type: -> "attachment-viewer"

    $scope.dialog = utils.openModal("/partials/attachment_viewer", AttachmentViewerController , resolveObj)

]

# translating rules
TranslateRulesController = ($scope, $modalInstance, Flow, utils, languages, ruleset, translation) ->

  $scope.translation = translation

  # clone our ruleset
  ruleset = utils.clone(ruleset)

  for rule in ruleset.rules

    if rule.category
      rule._translation = {category:{}, test:{}, label:{}}
      rule._translation.category['from'] = rule.category[Flow.flow.base_language]
      rule._translation.category['to'] = rule.category[Flow.language.iso_code]

      if typeof(rule.test.test) == "object"
        rule._translation.test['from'] = rule.test.test[Flow.flow.base_language]
        rule._translation.test['to'] = rule.test.test[Flow.language.iso_code]

    if ruleset.ruleset_type == 'wait_menu' and rule.label
      $scope.translation = translation
      rule._translation.label['from'] = rule.label[Flow.flow.base_language]
      rule._translation.label['to'] = rule.label[Flow.language.iso_code]

  $scope.ruleset = ruleset
  $scope.languages = languages
  $scope.language = Flow.language

  $scope.ok = ->

    inputs = []
    for rule in ruleset.rules
      if rule.category
        if rule._translation.category.to and rule._translation.category.to.strip().length > 0
          rule.category[Flow.language.iso_code] = rule._translation.category.to
        else
          delete rule.category[Flow.language.iso_code]

        if typeof(rule.test.test) == "object"

          if rule._translation.test.to and rule._translation.test.to.strip().length > 0
            rule.test.test[Flow.language.iso_code] = rule._translation.test.to
            inputs.push(rule._translation.test.to)
          else
            delete rule.test.test[Flow.language.iso_code]

    if $scope.hasInvalidFields(inputs)
      return true

    # USSD message translation save
    if Flow.flow.flow_type == 'U'
      ruleset.config.ussd_message[Flow.language.iso_code] = $scope.translation.to

    # USSD menu translation save
    if ruleset.ruleset_type == 'wait_menu'
      for rule in ruleset.rules
        if rule._translation.label.to and rule._translation.label.to.strip().length > 0
          rule.label[Flow.language.iso_code] = rule._translation.label.to
        else
          delete rule.label?[Flow.language.iso_code]

    Flow.replaceRuleset(ruleset)
    $modalInstance.close ""

  $scope.cancel = ->
    $modalInstance.dismiss "cancel"

# The controller for our translation modal
TranslationController = ($rootScope, $scope, $modalInstance, language, translations, Flow) ->
  $scope.translations = translations
  $scope.language = language

  $scope.ok = (translations) ->
    if $scope.hasInvalidFields((translation.to for translation in translations))
      return
    $modalInstance.close translations

  $scope.cancel = ->
    $modalInstance.dismiss "cancel"

NodeEditorController = ($rootScope, $scope, $modalInstance, $timeout, $log, Flow, flowController, Plumb, utils, options) ->
 
  # let our template know our editor type
  $scope.flow = Flow.flow
  $scope.nodeType = options.nodeType
  $scope.ivr = window.ivr
  $scope.ussd = window.ussd
  $scope.options = options

  $scope.contactFields = Flow.contactFieldSearch
  $scope.updateContactFields = Flow.updateContactSearch

  $scope.actionConfigs = Flow.actions
  $scope.rulesetConfigs = Flow.rulesets
  $scope.operatorConfigs = Flow.operators

  # all org languages except default
  $scope.languages = utils.clone(Flow.languages).filter (lang) -> lang.name isnt "Default"
  $scope.channels = Flow.channels

  formData = {}
  formData.resthook = ""

  if options.nodeType == 'rules' or options.nodeType == 'ivr'

    ruleset = options.ruleset
    formData.previousRules = ruleset.rules
    formData.groups = []

    for rule in ruleset.rules
      if rule.test.type == 'in_group'
        formData.groups.push(rule.test.test)

    # initialize our random categories
    if ruleset.ruleset_type == 'random'
      randomBuckets = []
      for rule in ruleset.rules
        if rule.test.type == 'between'
          randomBuckets.push
            name: rule.category
            destination: rule.destination
            _base: rule.category[Flow.flow.base_language]

      formData.randomBuckets = randomBuckets
      formData.buckets = randomBuckets.length

    # our placeholder actions if they flip
    action =
      type: defaultActionSetType()
      uuid: uuid()

    actionset =
      _switchedFromRule: true
      x: ruleset.x
      y: ruleset.y
      uuid: uuid(),
      exit_uuid: uuid(),
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
      ruleset_type: defaultRuleSetType(),
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

  formData.timeoutOptions = [
    {value:1, text:'1 minute'},
    {value:2, text:'2 minutes'},
    {value:3, text:'3 minutes'},
    {value:4, text:'4 minutes'},
    {value:5, text:'5 minutes'},
    {value:10, text:'10 minutes'},
    {value:15, text:'15 minutes'},
    {value:30, text:'30 minutes'},
    {value:60, text:'1 hour'},
    {value:120, text:'2 hours'},
    {value:180, text:'3 hours'},
    {value:360, text:'6 hours'},
    {value:720, text:'12 hours'},
    {value:1080, text:'18 hours'},
    {value:1440, text:'1 day'},
    {value:2880, text:'2 days'},
    {value:4320, text:'3 days'},
    {value:10080, text:'1 week'},
  ]

  minutes = 5
  formData.hasTimeout = false

  # check if we have a timeout rule present
  for rule in ruleset.rules
    if rule.test.type == 'timeout'
      minutes = rule.test.minutes
      formData.hasTimeout = true
      break

  # initialize our timeout options
  formData.timeout = formData.timeoutOptions[0]
  for option in formData.timeoutOptions
    if option.value == minutes
      formData.timeout = option

  formData.webhook_action = 'GET'
  if ruleset.config
    formData.webhook = ruleset.config.webhook
    formData.webhook_action = ruleset.config.webhook_action
    formData.webhook_headers = ruleset.config.webhook_headers or []
    formData.isWebhookAdditionalOptionsVisible = formData.webhook_headers.length > 0
  else
    formData.webhook_headers = []
    formData.isWebhookAdditionalOptionsVisible = false

  formData.rulesetConfig = Flow.getRulesetConfig({type:ruleset.ruleset_type})

  $scope.webhookAdditionalOptions = () ->
    if formData.isWebhookAdditionalOptionsVisible == true
      formData.isWebhookAdditionalOptionsVisible = false
    else
      formData.isWebhookAdditionalOptionsVisible = true

    if formData.webhook_headers.length == 0
      $scope.addNewWebhookHeader()

  $scope.addNewWebhookHeader = () ->
    if formData.webhook_headers == undefined
      formData.webhook_headers = []

    formData.webhook_headers.push({name: '', value: ''})

  $scope.removeWebhookHeader = (index) ->
    formData.webhook_headers.splice(index, 1)
    if formData.webhook_headers.length == 0
      $scope.addNewWebhookHeader()

  $scope.updateActionForm = (config) ->

    # when our action form changes, clear our invalid fields
    $scope.invalidFields = null

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

  INTERRUPTED_TYPE = 'interrupted_status'
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

  airtimeAmountConfig = []
  seenOrgCountries = []
  for country in angular.copy(Flow.channel_countries)
    countryAirtime = country
    countryCode = country.code
    if config[countryCode]
      countryAirtime.amount = parseFloat(config[countryCode]['amount'])
    else
      countryAirtime.amount = 0
    seenOrgCountries.push(countryCode)

    airtimeAmountConfig.push(countryAirtime)

  for countryCode, countryConfig of config
    if countryCode not in seenOrgCountries
      airtimeAmountConfig.push(countryConfig)

  formData.airtimeAmountConfig = airtimeAmountConfig

  if ruleset.config
    formData.flow = ruleset.config.flow
  else
    formData.flow = {}

  $scope.rulesetTypeChanged = () ->
    # when our ruleset form changes clear our invalid fields
    $scope.invalidFields = null

    if $scope.formData.rulesetConfig.type == "random"
      if not formData.buckets
        formData.buckets = 2
      $scope.updateRandomBuckets()

  $scope.updateRandomBuckets = () ->

    formData = $scope.formData
    if not formData.randomBuckets
      formData.randomBuckets = []

    # add any necessary groups
    for i in [formData.randomBuckets.length...formData.buckets] by 1
      formData.randomBuckets.push
        _base: "Bucket " + (i+1)

    # trim off any excess groups
    formData.randomBuckets.splice(formData.buckets)

  $scope.hasRules = () ->
    if $scope.formData.rulesetConfig
      return $scope.formData.rulesetConfig.type in Flow.supportsRules

  $scope.isRuleVisible = (rule) ->
    return flow.flow_type in rule._config.filter

  $scope.getFlowsUrl = (flow) ->
    url = "/flow/?_format=select2"
    if Flow.flow.flow_type == 'S'
      return url + "&flow_type=S"
    if Flow.flow.flow_type == 'F'
      return url + "&flow_type=F&flow_type=V"
    if Flow.flow.flow_type == 'V'
      return url + "&flow_type=V"
    return url

  $scope.isPausingRuleset = ->
    return Flow.isPausingRulesetType($scope.formData.rulesetConfig.type)

  $scope.remove = (rule) ->
    $scope.removed.push(rule)
    index = $scope.ruleset.rules.indexOf(rule)
    $scope.ruleset.rules.splice(index, 1)

  $scope.numericRule =
    test:
      type: 'between'
    config: Flow.getOperatorConfig('between')

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
    else if rule.test.type != "between" and rule.test.type != "ward"
      if rule.test.test
        if rule._config.localized
          rule.test._base = rule.test.test[Flow.flow.base_language]
        else
          rule.test =
            _base: rule.test.test

    # and finally the category name
    rule.category._base = rule.category[Flow.flow.base_language]

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
    return flow.flow_type in operator.filter

  $scope.isVisibleRulesetType = (rulesetConfig) ->
    valid = flow.flow_type in rulesetConfig.filter

    if (rulesetConfig.type == 'flow_field' or rulesetConfig.type == 'form_field') and $scope.flowFields.length == 0
      return false

    if rulesetConfig.type == 'contact_field' and $scope.contactFields.length == 0
      return false

    if rulesetConfig.type == 'airtime' and not $rootScope.hasAirtimeService
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
    else if op == "has_email"
      categoryName = "email"
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

  $scope.isRuleComplete = (rule) ->
    complete = true
    if not rule.category or not rule.category._base
      complete = false

    else if rule._config.operands == 1 and not rule.test._base
      complete = false

    else if rule._config.type == 'between' and (not rule.test.min or not rule.test.max)
      complete = false

    else if rule._config.type == 'ward' and (not rule.test.state or not rule.test.district)
      complete = false

    return complete

  stopWatching = $scope.$watch (->$scope.ruleset), ->
    complete = true
    for rule in $scope.ruleset.rules
      if rule._config.type in ['airtime_status','subflow','timeout', INTERRUPTED_TYPE]
        continue
      complete = complete and $scope.isRuleComplete(rule)
      if not complete
        break

    if complete and $scope.ruleset.ruleset_type != 'wait_menu'
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

  $scope.updateRules = (ruleset, rulesetConfig, splitEditor) ->

    rules = []
    if rulesetConfig.rules
      # find out the allowed rules for our ruleset
      validRules = {}
      for rule in rulesetConfig.rules
        validRules[rule.test.type] = true

      # collect our existing rules that are valid
      for rule in ruleset.rules
        if validRules[rule.test.type]
          rules.push(rule)

      # fill in any missing rules
      for rule in rulesetConfig.rules
        found = false
        for new_rule in ruleset.rules
          if angular.equals(new_rule.test, rule.test)
            found = true
            break

        # construct a new rule accordingly and add it
        if not found
          newRule =
            uuid: uuid()
            test: rule.test
            category: {}
          newRule.category[Flow.flow.base_language] = rule.name
          rules.push(newRule)

    # create or update our random bucket rules
    if ruleset.ruleset_type == 'random'
        rules = []
        size = 1.0 / $scope.formData.randomBuckets.length
        min = 0
        for bucket, idx in $scope.formData.randomBuckets
          if not bucket.name
            bucket.name = {}
          bucket.name[Flow.flow.base_language] = bucket._base

          rules.push
            uuid: uuid()
            test:
              type: 'between'
              min: "" + min
              max: "" + (min + size)
            category: bucket.name
            destination: bucket.destination
          min += size

    # group split ruleset
    if ruleset.ruleset_type == 'group'
      old_groups = {}

      # create a group_id -> rule map of our old groups
      if formData.previousRules
        for rule in formData.previousRules
          if rule.test.type == 'in_group'
            if rule.test.test.uuid
              old_groups[rule.test.test.uuid] = rule

      for group in splitEditor.omnibox.selected.groups

        # deal with arbitrary group adds
        if typeof group is 'string'
          group =
            name: group

        # if we have an old group, use that one
        if group.id and group.id of old_groups
          rules.push(old_groups[group.id])

        # otherwise create a new group
        else
          category = {}
          category[Flow.flow.base_language] = group.name

          # create a rule that works for existing or new groups
          rule =
            uuid: uuid()
            test:
              type: 'in_group'
              test:
                name: group.name
            category: category

          # if they picked an existing group, save its uuid too
          if group.id
            rule.test.test['uuid'] = group.id

          rules.push(rule)

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
          rule.category[Flow.flow.base_language] = option.category._base
          rules.push(rule)

    # rules configured from our select widgets
    if $scope.hasRules()

      for rule in ruleset.rules
        # we'll tack our everything and timeout rules on the end
        if rule._config.type in ['true', 'timeout', INTERRUPTED_TYPE]
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
            rule.test.test[Flow.flow.base_language] = rule.test._base
          else
            rule.test.test = rule.test._base

        rule.category[Flow.flow.base_language] = rule.category._base
        if rule.category
          rules.push(rule)

    # grab previous category translations and destinations if we have them
    otherRuleUuid = uuid()
    otherDestination = null

    timeoutRuleUuid = uuid()
    timeoutDestination = null

    timeoutCategory = {}
    timeoutCategory[Flow.flow.base_language] = 'No Response'

    interruptedRuleUuid = uuid()
    interruptedDestination = null

    interruptedCategory = {}
    interruptedCategory[Flow.flow.base_language] = "Interrupted"


    for rule in ruleset.rules
      if rule._config.type == 'true'
        otherDestination = rule.destination
        otherCategory = rule.category
        otherRuleUuid = rule.uuid
      else if rule._config.type == 'timeout'
        timeoutDestination = rule.destination
        timeoutCategory = rule.category
        timeoutRuleUuid = rule.uuid
      else if rule._config.type == INTERRUPTED_TYPE
        interruptedDestination = rule.destination
        interruptedCategory = rule.category
        interruptedRuleUuid = rule.uuid

    # if for some reason we don't have an other rule
    # create an empty category (this really shouldn't happen)
    if not otherCategory
      otherCategory = {}
    otherCategory[Flow.flow.base_language] = 'Other'

    # add an always true rule if not configured
    if not rulesetConfig.rules and not rulesetConfig.hide_other
      rules.push
        _config: Flow.getOperatorConfig("true")
        test:
          test: "true"
          type: "true"
        destination: otherDestination
        uuid: otherRuleUuid
        category: otherCategory

    if $scope.formData.hasTimeout and ruleset.ruleset_type == 'wait_message'
      rules.push
        _config: Flow.getOperatorConfig("timeout")
        test:
          type: "timeout"
          minutes: $scope.formData.timeout.value
        destination: timeoutDestination
        uuid: timeoutRuleUuid
        category: timeoutCategory

    # strip out exclusive rules if we have any
    rules = for rule in rules when Flow.isRuleAllowed($scope.ruleset.ruleset_type, rule.test.type) then rule

    # if there's only one rule, make our other be 'All Responses'
    if rules.length == 1 or (rules.length == 2 and rules[1].test.type == 'timeout')
      otherCategory[Flow.flow.base_language] = 'All Responses'

    # add interrupted rule for USSD ruleset
    if ruleset.ruleset_type in ['wait_menu', 'wait_ussd']
      rules.push
        _config: Flow.getOperatorConfig(INTERRUPTED_TYPE)
        test:
          test: "interrupted"
          type: INTERRUPTED_TYPE
        destination: interruptedDestination
        uuid: interruptedRuleUuid
        category: interruptedCategory

    $scope.ruleset.rules = rules

  $scope.okRules = (splitEditor) ->

    # track if any of our inputs are using invalid fields
    fieldChecks = []

    if formData.rulesetConfig.type == 'expression'
      fieldChecks.push($scope.ruleset.operand)

    if formData.rulesetConfig.type == 'webhook'
      fieldChecks.push(formData.webhook)
    
    for rule in $scope.ruleset.rules
      if rule.test._base
        fieldChecks.push(rule.test._base)
    
    if $scope.hasInvalidFields(fieldChecks)
      return

    # close our dialog
    stopWatching()
    $modalInstance.close ""

    $timeout ->
      # changes from the user
      ruleset = $scope.ruleset

      if not ruleset.config
        ruleset.config = {}

      formData = $scope.formData
      rulesetConfig = formData.rulesetConfig
      contactField = formData.contactField
      flowField = formData.flowField
      airtimeAmountConfig = formData.airtimeAmountConfig
      flow = formData.flow

      # save whatever ruleset type they are setting us to
      changedRulesetType = ruleset.ruleset_type != rulesetConfig.type
      ruleset.ruleset_type = rulesetConfig.type

      if rulesetConfig.type == 'subflow'
        flow = splitEditor.flow.selected[0]
        ruleset.config =
          flow:
            name: flow.text
            uuid: flow.id

      if rulesetConfig.type == 'random'
        ruleset.operand = '@(RAND())'

      # settings for a message form
      if rulesetConfig.type == 'form_field'
        ruleset.operand = '@flow.' + flowField.id
        ruleset.config.field_index = $scope.formData.fieldIndex.id
        ruleset.config.field_delimiter = $scope.formData.fieldDelimiter.id

      else if rulesetConfig.type == 'airtime'
        airtimeConfig = {}
        for elt in airtimeAmountConfig
          amount = elt.amount
          try
            elt.amount = parseFloat(amount)
          catch
            elt.amount = 0
          airtimeConfig[elt.code] = elt
        ruleset.config = airtimeConfig

      else if rulesetConfig.type == 'resthook'
        ruleset.config = {'resthook': splitEditor.resthook.selected[0]['id']}

      else if rulesetConfig.type == 'webhook'

        # don't include headers without a name
        webhook_headers = []
        for header in formData.webhook_headers
          if header.name
            webhook_headers.push(header)

        ruleset.config =
          webhook: formData.webhook
          webhook_action: formData.webhook_action
          webhook_headers: webhook_headers

      # update our operand if they selected a contact field explicitly
      else if rulesetConfig.type == 'contact_field'
        ruleset.operand = '@contact.' + contactField.id

      # or if they picked a flow field
      else if rulesetConfig.type == 'flow_field'
        ruleset.operand = '@flow.' + flowField.id

      # or just want to evaluate against a message
      else if rulesetConfig.type == 'wait_message'
        ruleset.operand = '@step.value'

      # update our rules accordingly
      $scope.updateRules(ruleset, rulesetConfig, splitEditor)

      # unplumb any rules that were explicitly removed
      Plumb.disconnectRules($scope.removed)

      # switching from an actionset means removing it and hijacking its connections
      connections = Plumb.getConnectionMap({ target: actionset.uuid })
      if ruleset._switchedFromAction
        Flow.removeActionSet($scope.actionset)

      # if the ruleset type changed, we should remove old one and create a new one
      if changedRulesetType
        connections = Plumb.getConnectionMap({ target: ruleset.uuid })
        Flow.removeRuleset(ruleset.uuid)
        ruleset.uuid = uuid()
        for rule in ruleset.rules
          rule.uuid = uuid()

      # save our new ruleset
      Flow.replaceRuleset(ruleset, false)

      # remove any connections that shouldn't be allowed
      for rule in ruleset.rules
        if rule.destination and not Flow.isConnectionAllowed(ruleset.uuid + '_' + rule.uuid, rule.destination)
          Flow.updateDestination($scope.ruleset.uuid + '_' + rule.uuid, null)

      # steal the old connections if we are replacing an actionset with ourselves
      if ruleset._switchedFromAction or changedRulesetType
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

  $scope.clickShowActionMedia = ->
    clicked = this
    action = clicked.action
    resolveObj =
      action: -> action
      type: -> "attachment-viewer"

    $scope.dialog = utils.openModal("/partials/attachment_viewer", AttachmentViewerController , resolveObj)

  $scope.onFileSelect = ($files, actionset, action) ->
    flowController.onFileSelect($files, actionset, action, false)

  $scope.cancel = ->
    stopWatching()
    $modalInstance.dismiss "cancel"

  #-----------------------------------------------------------------
  # Actions editor
  #-----------------------------------------------------------------
  $scope.action = utils.clone(action)
  $scope.showAttachOptions = false
  $scope.showAttachVariable = false

  if $scope.action._attachURL
    $scope.showAttachOptions = true
    $scope.showAttachVariable = true
  else
    $scope.action._attachType = "image"

  if $scope.options.dragSource? or !($scope.action.quick_replies? and $scope.action.quick_replies != undefined and $scope.action.quick_replies.length > 0)
    $scope.quickReplies = []
    $scope.showQuickReplyButton = true
  else
    $scope.quickReplies = $scope.action.quick_replies
    $scope.showQuickReplyButton = false

  formData.isActionWebhookAdditionalOptionsVisible = $scope.action.webhook_headers?.length > 0

  $scope.actionWebhookAdditionalOptions = () ->
    if formData.isActionWebhookAdditionalOptionsVisible == true
      formData.isActionWebhookAdditionalOptionsVisible = false
    else
      formData.isActionWebhookAdditionalOptionsVisible = true

    if $scope.action.webhook_headers.length == 0
      $scope.addNewActionWebhookHeader()

  $scope.addNewActionWebhookHeader = () ->
    if !$scope.action.webhook_headers
      $scope.action.webhook_headers = []
    $scope.action.webhook_headers.push({name: '', value: ''})

  $scope.removeActionWebhookHeader = (index) ->
    $scope.action.webhook_headers.splice(index, 1)
    if $scope.action.webhook_headers.length == 0
      $scope.addNewActionWebhookHeader()

  $scope.addNewQuickReply = ->
    $scope.showQuickReplyButton = false
    if $scope.quickReplies.length < 11
      addQuickReply = {}
      addQuickReply[$scope.base_language] = ''
      $scope.quickReplies.push(addQuickReply)

  $scope.removeQuickReply = (index) ->
    $scope.quickReplies.splice(index, 1)

    if $scope.quickReplies.length == 0
      $scope.showQuickReplyButton = true

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
    if $scope.hasInvalidFields([$scope.action.url])
      return

    $scope.action.type = 'play'
    Flow.saveAction(actionset, $scope.action)
    $modalInstance.close()

  $scope.removeAttachment = ->
    delete $scope.action['media']
    delete $scope.action['_media']
    delete $scope.action['_attachURL']

  # Saving a reply message in the flow
  $scope.saveMessage = (message, type='reply', hasAttachURL=false) ->

    inputs = [message]
    if hasAttachURL
      inputs.push($scope.action._attachURL)
    if $scope.hasInvalidFields(inputs)
      return

    if typeof($scope.action.msg) != "object"
      $scope.action.msg = {}

    $scope.action.msg[$scope.base_language] = message
    $scope.action.type = type

    if hasAttachURL and $scope.action._attachURL
      if not $scope.action.media
        $scope.action.media = {}

      $scope.action.media[$scope.base_language] = $scope.action._attachType + ':' + $scope.action._attachURL

      # make sure our localizations all have the same type
      for key in Object.keys($scope.action.media)
        if key != $scope.base_language
          translation = $scope.action.media[key]
          $scope.action.media[key] = $scope.action._attachType + ':' + translation.split(':')[1]
    
    else if not $scope.action._media
      delete $scope.action['media']

    if $scope.quickReplies.length > 0
      $scope.action.quick_replies = $scope.quickReplies
    else
      delete $scope.action['quick_replies']

    Flow.saveAction(actionset, $scope.action)
    $modalInstance.close()

  # Saving a message to somebody else
  $scope.saveSend = (omnibox, message) ->

    if $scope.hasInvalidFields([message])
      return

    groups = []
    for group in omnibox.groups
      groups.push
        uuid: group.id
        name: group.name
    $scope.action.groups = groups

    contacts = []
    for contact in omnibox.contacts
      contacts.push
        uuid: contact.id
        name: contact.name
    $scope.action.contacts = contacts

    $scope.action.variables = omnibox.variables
    $scope.action.type = 'send'

    if typeof($scope.action.msg) != "object"
      $scope.action.msg = {}
    $scope.action.msg[$scope.base_language] = message

    Flow.saveAction(actionset, $scope.action)
    $modalInstance.close()

  # Saving labels to add to a message
  $scope.saveLabels = (msgLabels) ->

    labels = []
    for msgLabel in msgLabels
      found = false
      for label in Flow.labels
        if label.id == msgLabel
          found = true
          labels.push
            uuid: label.id
            name: label.text

      if not found
        labels.push
          uuid: msgLabel.id
          name: msgLabel.text

    $scope.action.labels = labels
    
    $scope.action.type = 'add_label'
    Flow.saveAction(actionset, $scope.action)
    $modalInstance.close()


  # Saving the add to or remove from group actions
  $scope.saveGroups = (actionType, omnibox, allGroups) ->

    $scope.action.type = actionType

    groups = []
    if not allGroups
      for group in omnibox.groups
        if group.id and group.name
          groups.push
            uuid: group.id
            name: group.name
        else
          # other
          groups.push(group)

    $scope.action.msg = undefined
    $scope.action.groups = groups

    if not allGroups
      # add our list of variables
      for variable in omnibox.variables
        $scope.action.groups.push(variable.id)

    Flow.saveAction(actionset, $scope.action)
    $modalInstance.close()

  # Save the updating of a contact
  $scope.saveUpdateContact = (field, value) ->

    if $scope.hasInvalidFields([value])
      return

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

    if $scope.hasInvalidFields([url])
      return

    # don't include headers without name
    webhook_headers = []
    if $scope.action.webhook_headers
      for header in $scope.action.webhook_headers
        if header.name
          webhook_headers.push(header)

    $scope.action.type = 'api'
    $scope.action.action = method
    $scope.action.webhook = url
    $scope.action.webhook_headers = webhook_headers

    Flow.saveAction(actionset, $scope.action)
    $modalInstance.close()

  $scope.saveEmail = (addresses) ->

    if $scope.hasInvalidFields([$scope.action.subject, $scope.action.msg])
      return

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

      groups = []
      for group in omnibox.groups
        groups.push
          uuid: group.id
          name: group.name
      $scope.action.groups = groups

      contacts = []
      for contact in omnibox.contacts
        contacts.push
          uuid: contact.id
          name: contact.name
      $scope.action.contacts = contacts
      $scope.action.variables = omnibox.variables

    else
      $scope.action.type = 'flow'

    flow = flow[0]
    $scope.action.flow =
      uuid: flow.id
      name: flow.text

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

  $scope.saveChannel = () ->
    # look up the name for this channel, make sure it is up to date
    definition = {type: 'channel', channel: $scope.action.channel, uuid: $scope.action.uuid}
    for chan in Flow.channels
      if chan.uuid == $scope.action.channel
        definition['name'] = chan.name

    Flow.saveAction(actionset, definition)
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
      Flow.removeRuleset($scope.ruleset.uuid)

      $timeout ->
        for source of connections
          Flow.updateDestination(source, actionset.uuid)
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


AttachmentViewerController = ($scope, $modalInstance, action, type) ->
  $scope.action = action
  $scope.type = type

  $scope.cancel = ->
    $modalInstance.dismiss "cancel"
