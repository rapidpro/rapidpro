class window.AutoComplete

  KEY_LEFT = 37
  KEY_RIGHT = 39

  constructor: (@variables=[], @functions=[]) ->

    @parser = new window.excellent.Parser('@', ['channel', 'contact', 'date', 'extra', 'flow', 'step', 'parent', 'child', 'new_contact']);
    @completions = @variables.concat(@functions)
    @invalidFields = {}

    # mark our functions as functions
    for f in @functions
      f['function'] = true
      f['example'] = f['signature']

    ac = this

    @config =
      at: "@"
      insertBackPos: 1
      data: @variables
      searchKey: "name"
      insertTpl: @getInsertTemplate
      startWithSpace: true
      displayTpl: @getDisplayTemplate
      limit: 100
      maxLen: 100
      suffix: ""
      callbacks:

        highlighter: (li, query) -> return li

        matcher: (flag, subtext) ->
          return ac.parser.expressionContext(subtext)

        filter: (query, data, searchKey) ->

          if query and query[0] is '('
            data = ac.completions

          subQuery = ac.parseFilterQuery(query)
          lastIdx = if subQuery then subQuery.lastIndexOf('.') else -1
          start = subQuery.substring(0, lastIdx)
          results = ac.findCompletions(subQuery, data, start, lastIdx)

          return results

        sorter: (query, items, searchKey) ->

          # add an option to show our functions
          lastOptFunctions =
            'name': '('
            'display': "Functions",
            'function': true

          subQuery = ac.parseQuery(query);

          results = []
          for item in items
            if query
              item.order = new String(item[searchKey]).toLowerCase().indexOf(subQuery.toLowerCase())
              if item.order > -1
                results.push(item)
            else
              results.push(item)

          if not query or query.match(/[(.]/g) is null
            results.push(lastOptFunctions)

          results.sort (a,b) ->

            # prefer order if one is higher
            if a.order != b.order
              return a.order - b.order

            # otherwise, sort non-functions first
            if a.function and not b.function
              return 1
            else if b.function and not a.function
              return -1

            # lastly, just do alpha sort
            if (a.function and b.function) or (not a.function and not b.function)
              if a.name > b.name
                return 1
              else
                return -1

          return results

        tplEval: (tpl, map, action) ->

          template = tpl;
          query = this.query.text
          subQuery = ac.parseQuery(query)

          try
            template = tpl(map, query, subQuery)

            template.replace /\$\{([^\}]*)\}/g, (tag, key, pos) -> map[key]
          catch error
            return ""

        beforeInsert: (value, item) ->

          completionChars = new RegExp("([A-Za-z_\\d\.]*)$", 'gi')
          valueForName = ""
          match = completionChars.exec(value)
          if match
            valueForName = match[2] || match[1]

          hasMore = false
          for option in ac.variables
            hasMore = valueForName and option.name.indexOf(valueForName + '.') is 0 and option.name isnt valueForName
            if hasMore
              break

          value += '.' if hasMore

          isFunction = false
          for option in ac.functions
            isFunction = valueForName and option.name.indexOf(valueForName) is 0 and option.name is valueForName
            if isFunction
              break

          value += '()' if isFunction

          if valueForName is "" and value is '@('
            value += ')'
          else if valueForName and not hasMore and not isFunction
            value += " "

          return value

  findInvalidFields: (text) ->
    if not text
      return []

    # these are acceptable keys, that we don't necessarily want to show completion for
    validKeys = {
      "id": true,
      "telegram": true,
      "facebook": true
    }

    for variable in @variables
      if variable.name.startsWith('contact')
        key = variable.name.slice(8)
        if key
          validKeys[key] = true;

    fields = @parser.getContactFields(text)

    re = /[a-z][a-z0-9_]+/;
    for field in fields
      if !(field of validKeys) or !re.exec(field)
        @invalidFields[field] = true
    return Object.keys(@invalidFields)

  getInvalidFields: () ->
    return Object.keys(@invalidFields)

  getDisplayTemplate: (map, query, subQuery) ->
    template = "<li><div class='completion-dropdown'><div class='option-name'>${name}</div><small class='option-display'>${display}</small></div></li>"
    if typeof map.example isnt "undefined"
      template = "<li><div class='completion-dropdown'><div class='option-name'>${name}</div><div class='option-example'><div class='display-labels'>Example</div>${example}</div><div class='option-display'><div class='display-labels'>Summary</div>${display}</div></div></li>"

    template

  getInsertTemplate: (map, query, subQuery) ->
    if query and query[0] is '('
      if query.length is 1 and subQuery is ""
        template = '@(${name}'
      else
        regexp = new RegExp("@*" + subQuery + "$")
        template = ('@' + query).replace(regexp, '${name}')

    else
      regexp = new RegExp(subQuery + "$")
      template = ('@' + query).replace(regexp, '${name}')

    template

  parseFilterQuery: (query) ->
    if not query
      return query

    if query.match(/[(]*[^"]*["]/)
      if @parser.isInStringLiteral(query)
        return null;

    return @parser.autoCompleteContext(query) or ''

  parseQuery: (query) ->
    parsedQuery = @parseFilterQuery(query)
    if not parsedQuery
      return parsedQuery

    if parsedQuery[0] == '#'
      parsedQuery = parsedQuery.slice(1)

    parsedQuery

  findCompletions: (query, data, start, lastIdx, prependChar=undefined) ->

    matched = {}
    results = []
    justFirstResult = false

    if query[0] == '#'
      query = query.slice(1)
      justFirstResult = true

    for option in data
      if option.name.toLowerCase().indexOf(query.toLowerCase()) == 0
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

          if name.toLowerCase().indexOf(query.toLowerCase()) != 0
            continue

          display = null

        if name not of matched
          matched[name] = name

          matchingOption =
            name: name
            display: display

          for key in Object.keys(option)
            if key isnt 'name' and key isnt 'display'
              matchingOption[key] = option[key]

          results.push(matchingOption)

    if justFirstResult
      return results.slice(0,1)
    return results


  bind: (selector, variables=null) ->

    if variables
      @completions = variables.concat(@functions)

    inputor = $(selector).atwho(@config)
    inputor.atwho('run')

    # when an option is selected, insert the text and update the caret
    inputor.on 'inserted.atwho', (atEvent, li, browserEvent) ->
      content = inputor.val()
      caretPos = inputor.caret 'pos'
      subtext = content.slice 0, caretPos
      if subtext.match(/\(\)$/) isnt null
        inputor.caret('pos', subtext.length - 1)

    # hide autocomplete if user clicks in the input
    inputor.off('click.atwhoInner').on 'click.atwhoInner', (e) ->
      inputor.atwho('hide')

    # check for possible inserts when a key is pressed
    inputor.off('keyup.atwhoInner').on 'keyup.atwhoInner', (e) ->

      atwho = inputor.data('atwho')

      if atwho
        app = atwho.setContextFor('@')
        view = app.controller()?.view

        switch e.keyCode
          when KEY_LEFT, KEY_RIGHT
            if view.visible()
              app.dispatch(e)
            return
          else
            app.onKeyup(e)

        content = inputor.val()
        caretPos = inputor.caret 'pos'
        subtext = content.slice(0, caretPos)
        nextPart = content.slice(caretPos)
        if subtext.slice(-2) is '@(' and (not nextPart or nextPart.slice(0,1) is not ')')
          text = subtext + ')' + content.slice(caretPos + 1)
          inputor.val(text)
          inputor.caret('pos', caretPos)