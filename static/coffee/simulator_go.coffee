getRequest = ->
  return {
    events: [],
    assets: [],
    asset_server: {
      type_urls: {
        flow: 'http://localhost:8888/flow/assets/' + window.orgId + '/123/flow/',
        group: 'http://localhost:8888/flow/assets/' + window.orgId + '/123/group/'
        field: 'http://localhost:8888/flow/assets/' + window.orgId + '/123/field/'
      }
    }
  }

getStartRequest = ->
  scope = $("#ctlr").data('$scope')
  request = getRequest()
  request['events'] = [
    {
      type: "set_contact",
      created_on: new Date(),
      contact: {
        uuid: uuid(),
        name: "Eric Newcomer",
        urns: []
      }
    }
  ]

  request['trigger'] = {
    type: "manual",
    flow: {uuid: scope.flow.metadata.uuid, name: scope.flow.metadata.name}
    triggered_on: new Date()
  }
  return request


window.simStart = ->
  window.session = null
  window.resetForm()
  request = getStartRequest()
  $.post('/engine/flow/start', JSON.stringify(request)).done (results) ->
    window.session = results.session

    # first clear our body
    $(".simulator-body").html ""

    # the initial flow doesn't get a flow start event
    scope = $("#ctlr").data('$scope')
    window.addMessage("Entering the flow \"" + scope.flow.metadata.name + "\"", "log")

    window.updateResults(results)

window.sendUpdate = (postData) ->
  console.log(postData)

  request = getRequest()
  request['session'] = window.session
  request['events'] = [{
    type: "msg_received",
    text: postData.new_message,
    msg_uuid: uuid(),
    urn: "tel:+12065551212",
    created_on: new Date(),
    contact: window.session.contact
  }]

  $.post('/engine/flow/resume', JSON.stringify(request)).done (results) ->
    window.session = results.session
    window.updateResults(results)
    window.resetForm()

window.showModal = (title, body) ->
  modal = new ConfirmationModal(title, body);
  modal.show();

window.updateResults = (data) ->

  console.log(data)

  for log in data.log
    event = log.event

    if event.type == "send_msg"
      window.addMessage(event.text, "MT")
    else if event.type == "flow_triggered"
      window.addMessage("Entering the flow \"" + event.flow.name + "\"", "log")
    else if event.type == "save_flow_result"
      slugged = event.name.toLowerCase().replace(/([^a-z0-9]+)/g, '_')
      window.addMessage("Saving @flow." + slugged + " as \"" + event.value + "\"", "log")
    else if event.type == "update_contact"
      window.addMessage("Updated " + event.field_name + " to \"" + event.value + "\"", "log")
    else if event.type == "add_to_group"
      for group in event.groups
        window.addMessage("Added to group \"" + group.name + "\"", "log")
    else if event.type == "webhook_called"
      if event.status_code
        window.addMessage("Called " + event.url + " which returned a <a href='javascript:showModal(\"Webhook Results\", event.response);'>" + event.status_code + " response</a>.", "log")
      else
        window.addMessage("Couldn't reach " + event.url, "log")
    else if event.type == "error"
      window.addMessage('')

  $(".simulator-body").scrollTop($(".simulator-body")[0].scrollHeight)
  $("#simulator textarea").val("")

  if data.session.status == 'completed'
    # the initial flow doesn't get a flow exit event
    scope = $("#ctlr").data('$scope')
    window.addMessage("Exited the flow \"" + scope.flow.metadata.name + "\"", "log")
