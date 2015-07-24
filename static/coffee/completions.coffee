window.matcher = (flag, subtext) ->
  regexp = new RegExp("(?:^|\\s)@([()A-Za-z_\.\+]*(?:[ ]*[+][ ]*[()A-Za-z_,\.\+]*|,[ ]*[()A-Za-z_,\.\+]*|$)*)$", "gi")
  match = regexp.exec(subtext)
  if match
    match[2] || match[1]
  else
    null

window.findContextQuery = (query) ->
  numPar = 0

  if not query
    return query

  if query.match(/^\($/g) isnt null or query.match(/,[ ]+$/g) isnt null
    return ""

  while query.match(/[,)]$/g) isnt null
    query = query.slice(0, -1)
    numPar += 1

  lastOpenPar = query.length
  while numPar > 0
    lastOpenPar = query.lastIndexOf("(")
    query = query.slice(0, lastOpenPar)
    numPar -= 1

  queryRegex = new RegExp("([A-Za-z_\d\.]*)(?:[), ]*)?$", "gi");
  match = queryRegex.exec(query);
  if match
    match[1] || match[0];
  else
    null

window.findMatches = (query, data, start, lastIdx, prependChar = undefined ) ->

  matched = {};
  results = [];

  for option in data
    if option.name.indexOf(query) == 0
      nextDot = option.name.indexOf('.', lastIdx + 1)
      if nextDot == -1

        if prependChar
          name = start + prependChar + option.name
        else
          name = option.name

        display = option.display
      else
        name = ""
        suffix = option.name.substring(lastIdx+1, nextDot)
        if start.length > 0 and start != suffix
          name = start + "."
        name += suffix

        if name.indexOf(query) != 0
          continue

        display = null

      if name not of matched
        matched[name] = name
        results.push({ name: name, display: display })

  return results

window.filter = (query, data, searchKey) ->

  if query and query[0] is '('
    data = variables_and_functions

  contextQuery = findContextQuery query
  lastIdx = contextQuery.lastIndexOf '.'
  start = contextQuery.substring 0, lastIdx
  results = findMatches contextQuery, data, start, lastIdx

  return results if results.length > 0

  regexp = new RegExp("([A-Za-z0-9_+-.]*\\|)([A-Za-z0-9_+-.]*)", "gi")
  match = regexp.exec(contextQuery)

  if match
    name = contextQuery.substring 0, q.indexOf '|'
    found = false;
    for item in data
      if item.name is name
        found = true
        break

    return results unless found

    lastIdx = contextQuery.lastIndexOf('|') + 1;
    start = contextQuery.substring 0, lastIdx - 1
    filterQuery = contextQuery.substring lastIdx
    results = findMatches filterQuery, filters, start, contextQuery.lastIndexOf '|', '|'


window.sorter = (query, items, searchKey) ->

  lastOptFunctions =
    'name': '('
    'display': "Functions"

  unless query
    items.push(lastOptFunctions)
    return items

  contextQuery = findContextQuery(query);

  _results = []
  for item in items
    item.atwho_order = new String(item[searchKey]).toLowerCase().indexOf contextQuery.toLowerCase()
    _results.push item if item.atwho_order > -1

  if query.match(/[(.]/g) is null
    _results.push(lastOptFunctions)

  _results.sort (a,b) -> a.atwho_order - b.atwho_order


window.beforeInsert = (value, item) ->

  completionChars = new RegExp("([A-Za-z_\d\.]*)$", 'gi')
  valueForName = ""
  match = completionChars.exec(value)
  if match
    valueForName = match[2] || match[1]

  data_variables = variables
  hasMore = false
  for option in data_variables
    hasMore = valueForName  and option.name.indexOf(valueForName) is 0 and option.name isnt valueForName
    break if hasMore

  value += '.' if hasMore

  data_functions = functions
  isFunction = false
  for option in data_functions
    isFunction = valueForName and option.name.indexOf(valueForName) is 0 and option.name is valueForName
    break if isFunction

  value += '()' if isFunction

  if valueForName is "" and value is '@('
    value += ')'
  else if valueForName and not hasMore and not isFunction
    value += " "

  value

window.tplval = (tpl, map, action) ->

  template = tpl;

  query = this.query.text
  contextQuery = findContextQuery query

  if action is 'onInsert'
    if query and query[0] is '(' and query.length is 1 and contextQuery is ""
      template = '@(${name}'
    else
      regexp = new RegExp(contextQuery + "$")
      template = ('@' + query).replace(regexp, '${name}')

  try
    template = tpl(map) unless typeof tpl is 'string'

    if typeof map.example isnt undefined and map.name is contextQuery and action is "onDisplay"
      template = "<li><h5>${name}</h5><div>${example}</div><div>${hint}</div><div>BLABLABLABAL</div></li>"

    template.replace /\$\{([^\}]*)\}/g, (tag, key, pos) -> map[key]
  catch error
    ""