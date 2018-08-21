videojs.newButton = videojs.Button.extend(init: (player, options) ->
  videojs.Button.call this, player, options
  @on 'click', @onClick
  return
)

videojs.newButton::onClick = ->
  #Add click routine here..
  return

#Creating New Button

createNewButton = ->
  props = 
    className: 'vjs-new-button vjs-control'
    innerHTML: '<div class="vjs-control-content">' + 'New' + '</div>'
    role: 'button'
    'aria-live': 'polite'
    tabIndex: 0
  videojs.Component::createEl null, props

#Adding the newly created button to Control Bar
videojs.plugin 'downloadButton', ->
  options = 'el': createNewButton()
  newButton = new (videojs.newButton)(this, options)
  @controlBar.el().appendChild newButton.el()
  return
