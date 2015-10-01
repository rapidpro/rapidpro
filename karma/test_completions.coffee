describe 'Matcher:', ->

  matcher = new AutoComplete().config.callbacks.matcher

  it 'should match "" after flag', ->
    matched = matcher "@", "some texts before @"
    expect(matched).toBe("")

  it 'should not match after @@ (escaped texts)', ->
    matched = matcher "@", "some texts before @@contact"
    expect(matched).toBe(null)

  it 'should not match after @@@@', ->
    matched = matcher "@", "some texts before @@@@contact"
    expect(matched).toBe(null)

  it 'should match after @@@', ->
    matched = matcher "@", "some texts before @@@contact"
    expect(matched).toBe("contact")

  it 'should match after @@@@@', ->
    matched = matcher "@", "some texts before @@@contact"
    expect(matched).toBe("contact")

  it 'should match variable after flag', ->
    matched = matcher "@", "some texts before @contact"
    expect(matched).toBe("contact")

  it 'should match variables with dot', ->
    matched = matcher "@", "some texts before @contact.born"
    expect(matched).toBe("contact.born")

  it 'should match variables with dot as big as possible', ->
    matched = matcher "@", "some texts before @contact.born.where.location"
    expect(matched).toBe("contact.born.where.location")

  it 'should not match space if we have a space at the end', ->
    matched = matcher "@", "some texts before @contact "
    expect(matched).toBe(null)

  it 'should not match space if if last word does not have flag', ->
    matched = matcher "@", "some texts before @contact contact"
    expect(matched).toBe(null)

  it 'should match functions', ->
    matched = matcher "@", "some texts before @(SUM"
    expect(matched).toBe("(SUM")

  it 'should not match escaped functions', ->
    matched = matcher "@", "some texts before @@(SUM"
    expect(matched).toBe(null)

  it 'should match all the function', ->
    matched = matcher "@", "some texts before @(SUM()"
    expect(matched).toBe("(SUM()")

  it 'should match the function as long as possible', ->
    matched = matcher "@", "some texts before @(SUM(contact.age, step.value"
    expect(matched).toBe("(SUM(contact.age, step.value")

  it 'should match the function as long as possible, may commas, underscores', ->
    matched = matcher "@", "some texts before @(SUM(contact.age, step.value, date.now_time"
    expect(matched).toBe("(SUM(contact.age, step.value, date.now_time")

  it 'should match the function as long as possible expression', ->
    matched = matcher "@", "some texts before @(SUM(contact.age, step.value)"
    expect(matched).toBe("(SUM(contact.age, step.value)")

  it 'should not match outside after max possible expression', ->
    matched = matcher "@", "some texts before @(SUM(contact.age, step.value))"
    expect(matched).toBe(null)

  it 'should not match outside after max possible expression', ->
    matched = matcher "@", "some texts before @(SUM(contact.age, step.value)))"
    expect(matched).toBe(null)

  it 'should not match if space after last )', ->
    matched = matcher "@", "some texts before @(SUM(contact.age, step.value)))) "
    expect(matched).toBe(null)

describe 'find context query', ->

  ac = new AutoComplete()

  it 'should return if not query', ->
    expect(ac.parseQuery('')).toBe('')
    expect(ac.parseQuery(null)).toBe(null)
    expect(ac.parseQuery(undefined)).toBe(undefined)

  it 'ignore first ( and return empty string', ->
    expect(ac.parseQuery('(')).toBe("")

  it 'should be the same for variables', ->
    expect(ac.parseQuery("contact")).toBe('contact')
    expect(ac.parseQuery("contact.age")).toBe('contact.age')
    expect(ac.parseQuery("contact.added_on")).toBe('contact.added_on')

  ###
  it 'no ( for function only', ->
    ctxtQuery = findContextQuery "(SUM"
    expect(ctxtQuery).toBe('SUM')

  it 'ignore ( after function', ->
    ctxtQuery = findContextQuery "(SUM("
    expect(ctxtQuery).toBe('SUM')

  it 'ignore ( followed by spaces after function', ->
    ctxtQuery = findContextQuery "(SUM( "
    expect(ctxtQuery).toBe('SUM')

    ctxtQuery = findContextQuery "(SUM(  "
    expect(ctxtQuery).toBe('SUM')
  ###

  it 'should give the last variable', ->
    expect(ac.parseQuery("(SUM(contact.date_added")).toBe('contact.date_added')

  ###
  it 'should return function after comma', ->
    ctxtQuery = findContextQuery "(SUM(contact.date_added,"
    expect(ctxtQuery).toBe('SUM')

  it 'should return empty string after comma followed by space', ->
    ctxtQuery = findContextQuery "(SUM(contact.date_added,  "
    expect(ctxtQuery).toBe('SUM')

  it 'should ignore function out of balanced paratheses', ->
    ctxtQuery = findContextQuery "(SUM(contact.date_added, step)"
    expect(ctxtQuery).toBe('SUM')

    ctxtQuery = findContextQuery "(SUM(contact.date_added, ABS(step.value)"
    expect(ctxtQuery).toBe('ABS')

    ctxtQuery = findContextQuery "(SUM(contact.date_added, ABS(step.value))"
    expect(ctxtQuery).toBe('SUM')
  ###

  it 'should not include previous (', ->
    expect(ac.parseQuery("(contact.age")).toBe('contact.age')

describe 'Find matches:', ->

  variables = [{"name":"contact", "display":"Contact Name"}, {"name":"channel", "display":"Channel Name"},
          {"name":"contact.age", "display":"Contact Age"}, {"name":"contact.name", "display":"Contact Name"},
          {"name":"contact.urn.path.tel.number", "display":"Contact URN tel number"},
          {"name":"channel.address", "display":"Channel Address"}]

  ac = new AutoComplete(variables)

  it 'should give unique first parts for empty string query', ->
    results = ac.findCompletions('', variables, '', -1)
    expected = [{"name":"contact", "display":"Contact Name"}, {"name":"channel", "display":"Channel Name"}]
    expect(results).toEqual(expected)

  it 'should match first parts filtered', ->
    results = ac.findCompletions('c', variables, '', -1)
    expected = [{"name":"contact", "display":"Contact Name"}, {"name":"channel", "display":"Channel Name"}]
    expect(results).toEqual(expected)

    results = ac.findCompletions('con', variables, '', -1)
    expected = [{"name":"contact", "display":"Contact Name"}]
    expect(results).toEqual(expected)

    results = ac.findCompletions('CON', variables, '', -1)
    expected = [{"name":"contact", "display":"Contact Name"}]
    expect(results).toEqual(expected)

    results = ac.findCompletions('CoN', variables, '', -1)
    expected = [{"name":"contact", "display":"Contact Name"}]
    expect(results).toEqual(expected)

  it 'should start showing second ; only show display if name is full', ->
    results = ac.findCompletions('contact.', variables, 'contact', 7)
    expected = [
        {"name":"contact.age", "display":"Contact Age"}, {"name":"contact.name", "display":"Contact Name"},
        {"name":"contact.urn", "display":null}]
    expect(results).toEqual(expected)

  it 'should start showing thirt parts...; only show display if name is full', ->
    results = ac.findCompletions('contact.urn.pa', variables, 'contact.urn', 11)
    expected = [{"name":"contact.urn.path", "display":null}]
    expect(results).toEqual(expected)


describe 'Sorter:', ->

  ac = new AutoComplete()
  sorter = ac.config.callbacks.sorter

  it 'should include "Functions" for null query', ->
    items = []
    results = sorter(null, items, 'name')
    expect(results).toEqual([{'name': '(', 'display': 'Functions', function:true}])

  it 'should include "Functions" for empty query', ->
    items = []
    results = sorter("", items, 'name')
    expect(results).toEqual([{'name': '(', 'display':'Functions', function:true}])

  it 'should include "Functions" for null query', ->
    items = [{'name':'contact.addition', 'display':'Contact Addition'},
             {'name':'econtact.added', 'display':'e-Contact Added'}]
    results = sorter(null, items, 'name')
    expect(results).toEqual([{'name': 'contact.addition', 'display': 'Contact Addition'},
                             {'name': 'econtact.added', 'display': 'e-Contact Added'},
                             {'name': '(', 'display': 'Functions', function:true}])

  it 'should include "Functions" for empty query', ->
    items = [{'name':'contact.addition', 'display':'Contact Addition'},
             {'name':'econtact.added', 'display':'e-Contact Added'}]
    results = sorter("", items, 'name')
    expect(results).toEqual([{'name':'contact.addition', 'display':'Contact Addition'},
                             {'name':'econtact.added', 'display':'e-Contact Added'},
                             {'name': '(', 'display':'Functions', function:true}])

  it 'should include "Functions" for query that do not have a dot', ->
    items = [{'name':'contact.addition', 'display':'Contact Addition'},
             {'name':'econtact.added', 'display':'e-Contact Added'}]
    results = sorter("contact", items, 'name')
    expect(results).toEqual([{'name':'contact.addition', 'display':'Contact Addition', 'order': 0},
                             {'name':'econtact.added', 'display':'e-Contact Added', 'order': 1},
                             {'name': '(', 'display':'Functions', function:true}])

  it 'should not include "Functions" for query that have a dot', ->
    items = [{'name':'econtact.added', 'display':'e-Contact Added'},
             {'name':'contact.addition', 'display':'Contact Addition'}]
    results = sorter("contact.add", items, 'name')
    expect(results).toEqual([{'name':'contact.addition', 'display':'Contact Addition', 'order':0},
                             {'name':'econtact.added', 'display':'e-Contact Added', 'order':1}])

  it 'should sort functions last', ->
    items = [{'name':'ABS', 'display':'Absolute Value', function:true},
             {'name':'econtact.added', 'display':'e-Contact Added'},
             {'name':'contact.addition', 'display':'Contact Addition'}]
    results = sorter("(", items, 'name')
    expect(results).toEqual([{'name':'contact.addition', 'display':'Contact Addition', 'order':0},
                             {'name':'econtact.added', 'display':'e-Contact Added', 'order':0},
                             {'name':'ABS', 'display':'Absolute Value', function:true, 'order':0}])

describe 'Filter:', ->

  ac = new AutoComplete()
  filter = ac.config.callbacks.filter

  data = [{"name":"contact", "display":"Contact Name"}, {"name":"channel", "display":"Channel Name"},
          {"name":"contact.age", "display":"Contact Age"}, {"name":"contact.name", "display":"Contact Name"},
          {"name":"contact.urn.path.tel.number", "display":"Contact URN tel number"},
          {"name":"channel.address", "display":"Channel Address"}]

  it 'should filter using findMatches', ->
    spyOn(ac, "findCompletions").and.callThrough()
    results = filter "", data, 'name'
    expected = [{"name":"contact", "display":"Contact Name"}, {"name":"channel", "display":"Channel Name"}]
    expect(results).toEqual(expected)
    expect(ac.findCompletions).toHaveBeenCalledWith("", data, "", -1)

    results = filter "con", data, 'name'
    expected = [{"name":"contact", "display":"Contact Name"}]
    expect(results).toEqual(expected)
    expect(ac.findCompletions).toHaveBeenCalledWith("con", data, "", -1)

    results = filter "contact.a", data, 'name'
    expected = [{"name":"contact.age", "display":"Contact Age"}]
    expect(results).toEqual(expected)
    expect(ac.findCompletions).toHaveBeenCalledWith("contact.a", data, "contact", 7)

describe 'beforeInsert:', ->

  beforeInsert = null

  beforeEach ->
    variables = [
      {"display": "New Contact", "name": "new_contact"}
      {"display": "Contact Name", "name": "contact"}
      {"display": "Contact Name", "name": "contact.name"}
      {"display": "Contact First Name", "name": "contact.first_name"}
    ]

    functions =[
      {"display": "Display Returns the sum of all arguments", "description": "Description: Returns the sum of all arguments", "name": "SUM", "hint": "Hint: Returns the sum of all arguments", "example": "SUM(args)", "arguments": [{"name": "args", "hint": "Hint for :-:args:-: arg"}]}
      {"display": "Display: Defines a time value", "description": "Description: Defines a time value", "name": "TIME", "hint": "Hint: Defines a time value", "example": "TIME(hours, minutes, seconds)","arguments": [{"name": "hours", "hint": "Hint for :-:hours:-: arg"}, {"name": "minutes","hint": "Hint for :-:minutes:-: arg"}]}
    ]

    ac = new AutoComplete(variables, functions)
    beforeInsert = ac.config.callbacks.beforeInsert

  it 'should append a space for variables without more option', ->
    expect(beforeInsert("@new_contact")).toBe("@new_contact ")

  it 'should append a dot if value is from variables and we have more options', ->
    expect(beforeInsert('@contact', [])).toBe("@contact.")

  it 'should balance parantheses', ->
    expect((beforeInsert "@(", [])).toBe("@()")

  it 'should append parantheses for function', ->
    expect(beforeInsert("@(SUM", [])).toBe("@(SUM()")

  it 'should allow internal @ in string literals', ->
    expect(beforeInsert('@(IF(flow.show_twitter, "@nyaruka"')).toBe('@(IF(flow.show_twitter, "@nyaruka"')

  # it 'should not allow internal @ inside expression', ->
  #   expect(beforeInsert("@(MAX(@flow.response_1")).toBe("@(MAX(flow.response_1")


describe 'tplEval:', ->

  ac = new AutoComplete()
  tplEval = ac.config.callbacks.tplEval

  beforeEach ->
    window.query =
      text: null

  describe 'onInsert', ->
    it 'should insert ( before name of map in the inserted text if query is just ( with length 1', ->
      window.query.text = "("
      inserted = tplEval(ac.getInsertTemplate, {'name': "SUM"}, "onInsert")
      expect(inserted).toBe('@(SUM')

    it 'should not insert ( before name inserted if query has length more than 1', ->
      window.query.text ="(SUM(con"
      expect(tplEval(ac.getInsertTemplate, {'name': "contact.name"}, "onInsert")).toBe('@(SUM(contact.name')
      expect(tplEval(ac.getInsertTemplate, {'name': "new_contact"}, "onInsert")).toBe('@(SUM(new_contact')

    it 'should omit @ preceding variables inside function arguments', ->
      window.query.text ="(SUM(@con"
      expect(tplEval(ac.getInsertTemplate, {'name': "contact.name"}, "onInsert")).toBe('@(SUM(contact.name')
      expect(tplEval(ac.getInsertTemplate, {'name': "new_contact"}, "onInsert")).toBe('@(SUM(new_contact')

    it 'should use the default tpl', ->
      window.query.text = "cont"

      expect(tplEval(ac.getInsertTemplate, {'name': "contact.name"}, "onInsert")).toBe('@contact.name')
      window.query.text = '(SUM(contact.name, ste'
      inserted = tplEval(ac.getInsertTemplate, {'name': "step.value"}, "onInsert")
      expect(inserted).toBe('@(SUM(contact.name, step.value')

  describe 'onDisplay', ->
    it 'should use the li', ->
      window.query.text = "cont"
      displayed = tplEval(ac.getDisplayTemplate, {'name':'contact.name', 'display':'Contact Name'}, 'onDisplay')
      expect(displayed).toBe("<li><div class='completion-dropdown'><div class='option-name'>contact.name</div><small class='option-display'>Contact Name</small></div></li>")

    it 'should switch template if we have example in map', ->
      window.query.text = "(SUM(contact,"
      displayed = tplEval(ac.getDisplayTemplate, {'name':'SUM', 'example':'SUM(A, B)', 'hint':'hint for sum', 'display':'SUM of numbers'}, 'onDisplay')
      expect(displayed).toBe("<li><div class='completion-dropdown'><div class='option-name'>SUM</div><div class='option-example'><div class='display-labels'>Example</div>SUM(A, B)</div><div class='option-display'><div class='display-labels'>Summary</div>SUM of numbers</div></div></li>")


describe 'getDisplayTemplate:', ->

  ac = new AutoComplete()
  it 'should not include example div if not example data is in the map', ->
    output = "<li><div class='completion-dropdown'><div class='option-name'>${name}</div><small class='option-display'>${display}</small></div></li>"
    expect(ac.getDisplayTemplate({'name':'contact.name', 'display':'Contact Name'}, 'cont', '')).toBe(output)

  it 'should include example div if example is defined in the map', ->
    output = "<li><div class='completion-dropdown'><div class='option-name'>${name}</div><div class='option-example'><div class='display-labels'>Example</div>${example}</div><div class='option-display'><div class='display-labels'>Summary</div>${display}</div></div></li>"
    expect(ac.getDisplayTemplate({'name':'SUM', 'example':'SUM(A, B)', 'hint':'hint for sum', 'display':'SUM of numbers'}, 'SUM', '')).toBe(output)

