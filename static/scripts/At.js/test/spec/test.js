(function () {
  'use strict';

  describe('matcher tests', function () {
    var matched;

    it('should match "" after flag', function () {
      matched = matcher("@", "some texts before @");
      expect(matched).toBe("");
    });

    it('should not match escaped texts', function () {
      matched = matcher("@", "some texts before @@contact");
      expect(matched).toBe(null);
    });

    it('should match variable after flag', function () {
      matched = matcher("@", "some texts before @contact");
      expect(matched).toBe("contact");
    });

    it('should match variables with dot', function () {
      matched = matcher("@", "some texts before @contact.born");
      expect(matched).toBe("contact.born");
    });

    it('should match variables with dot as big as possible', function () {
      matched = matcher("@", "some texts before @contact.born.where.location");
      expect(matched).toBe("contact.born.where.location");
    });

    it('should not match space if we have a space at the end', function () {
      matched = matcher("@", "some texts before @contact ");
      expect(matched).toBe(null);
    });

    it('should not match space if if last word does not have flag', function () {
      matched = matcher("@", "some texts before @contact contact");
      expect(matched).toBe(null);
    });

    it('should match functions', function () {
      matched = matcher("@", "some texts before @(SUM");
      expect(matched).toBe("(SUM");
    });

    it('should not match escaped functions', function () {
      matched = matcher("@", "some texts before @@(SUM");
      expect(matched).toBe(null);
    });

    it('should match all the function', function () {
      matched = matcher("@", "some texts before @(SUM()");
      expect(matched).toBe("(SUM()");
    });

    it('should match the function as long as possible', function () {
      matched = matcher("@", "some texts before @(SUM(contact.age, step.value");
      expect(matched).toBe("(SUM(contact.age, step.value");
    });

    it('should match the function as long as possible, may commas, underscores', function () {
      matched = matcher("@", "some texts before @(SUM(contact.age, step.value, date.now_time");
      expect(matched).toBe("(SUM(contact.age, step.value, date.now_time");
    });

    it('should match the function as long as possible', function () {
      matched = matcher("@", "some texts before @(SUM(contact.age, step.value))))");
      expect(matched).toBe("(SUM(contact.age, step.value))))");
    });

    it('should not match if space after last )', function () {
      matched = matcher("@", "some texts before @(SUM(contact.age, step.value)))) ");
      expect(matched).toBe(null);
    });
  });

  describe('find context query', function () {
    var ctxtQuery;

    it('should return if not query', function () {
      ctxtQuery = findContextQuery('');
      expect(ctxtQuery).toBe('');

      ctxtQuery = findContextQuery(null);
      expect(ctxtQuery).toBe(null);

      ctxtQuery = findContextQuery(undefined);
      expect(ctxtQuery).toBe(undefined);
    });

    it('ignore first ( and return empty string', function () {
      ctxtQuery = findContextQuery('(');
      expect(ctxtQuery).toBe("");
    });

    it('should be the same for variables', function () {
      ctxtQuery = findContextQuery("contact");
      expect(ctxtQuery).toBe('contact');

      ctxtQuery = findContextQuery("contact.age");
      expect(ctxtQuery).toBe('contact.age');

      ctxtQuery = findContextQuery("contact.added_on");
      expect(ctxtQuery).toBe('contact.added_on');
    });

    it('no ( for function only', function () {
      ctxtQuery = findContextQuery("(SUM");
      expect(ctxtQuery).toBe('SUM');
    });

    it('should give the last variable', function () {
      ctxtQuery = findContextQuery("(SUM(contact.date_added");
      expect(ctxtQuery).toBe('contact.date_added');
    });

    it('should return function after comma', function () {
      ctxtQuery = findContextQuery("(SUM(contact.date_added,");
      expect(ctxtQuery).toBe('SUM');
    });

    it('should return empty string after comma followed by space', function () {
      ctxtQuery = findContextQuery("(SUM(contact.date_added,  ");
      expect(ctxtQuery).toBe('');
    });

    it('should ignore function out of balanced paratheses', function () {
      ctxtQuery = findContextQuery("(SUM(contact.date_added, step)");
      expect(ctxtQuery).toBe('SUM');

      ctxtQuery = findContextQuery("(SUM(contact.date_added, ABS(step.value)");
      expect(ctxtQuery).toBe('ABS');

      ctxtQuery = findContextQuery("(SUM(contact.date_added, ABS(step.value))");
      expect(ctxtQuery).toBe('SUM');
    });

    it('should not include previous (', function () {
      ctxtQuery = findContextQuery("(contact.age");
      expect(ctxtQuery).toBe('contact.age');
    });

  });

  describe('Find matches', function () {
    var data = [{"name":"contact", "display":"Contact Name"}, {"name":"channel", "display":"Channel Name"},
      {"name":"contact.age", "display":"Contact Age"}, {"name":"contact.name", "display":"Contact Name"},
      {"name":"contact.urn.path.tel.number", "display":"Contact URN tel number"},
      {"name":"channel.address", "display":"Channel Address"}
    ];

    var results, expected;

    it('should give unique first parts for empty string query', function () {
      results = findMatches('', data, '', -1);
      expected = [{"name":"contact", "display":"Contact Name"}, {"name":"channel", "display":"Channel Name"}];
      expect(results).toEqual(expected);
    });

    it('should match first parts filtered', function() {
      results = findMatches('c', data, '', -1);
      expected = [{"name":"contact", "display":"Contact Name"}, {"name":"channel", "display":"Channel Name"}];
      expect(results).toEqual(expected);

      results = findMatches('con', data, '', -1);
      expected = [{"name":"contact", "display":"Contact Name"}];
      expect(results).toEqual(expected);
    });

    it('should start showing second ; only show display if name is full', function () {
      results = findMatches('contact.', data, 'contact', 7);
      expected = [
        {"name":"contact.age", "display":"Contact Age"}, {"name":"contact.name", "display":"Contact Name"},
        {"name":"contact.urn", "display":null}];
      expect(results).toEqual(expected);
    });

    it('should start showing thirt parts...; only show display if name is full', function () {
      results = findMatches('contact.urn.pa', data, 'contact.urn', 11);
      expected = [
        {"name":"contact.urn.path", "display":null}];
      expect(results).toEqual(expected);
    });
  });

  describe('Sorter', function () {
    var items, results;

    it('should include "Functions" for null query', function () {
      items = [];
      results = sorter(null, items, 'name');
      expect(results).toEqual([{'name': '(', 'display': 'Functions'}]);

    });

    it('should include "Functions" for empty query', function () {
      items = [];
      results = sorter("", items, 'name');
      expect(results).toEqual([{'name': '(', 'display':'Functions'}]);

    });

    it('should include "Functions" for null query', function () {
      items = [{'name':'contact.addition', 'display':'Contact Addition'}, {'name':'econtact.added', 'display':'e-Contact Added'}];
      results = sorter(null, items, 'name');
      expect(results).toEqual([{'name': 'contact.addition', 'display': 'Contact Addition'},
        {'name': 'econtact.added', 'display': 'e-Contact Added'},
        {'name': '(', 'display': 'Functions'}]);
    });

    it('should include "Functions" for empty query', function () {
      items = [{'name':'contact.addition', 'display':'Contact Addition'}, {'name':'econtact.added', 'display':'e-Contact Added'}];
      results = sorter("", items, 'name');
      expect(results).toEqual([{'name':'contact.addition', 'display':'Contact Addition'},
                               {'name':'econtact.added', 'display':'e-Contact Added'},
                               {'name': '(', 'display':'Functions'}]);
    });


    it('should include "Functions" for query that do not have a dot', function () {
      items = [{'name':'contact.addition', 'display':'Contact Addition'}, {'name':'econtact.added', 'display':'e-Contact Added'}];
      results = sorter("contact", items, 'name');
      expect(results).toEqual([{'name':'contact.addition', 'display':'Contact Addition', 'atwho_order': 0},
                               {'name':'econtact.added', 'display':'e-Contact Added', 'atwho_order': 1},
                               {'name': '(', 'display':'Functions'}]);

    });

    it('should not include "Functions" for query that have a dot', function () {
      items = [{'name':'contact.addition', 'display':'Contact Addition'}, {'name':'econtact.added', 'display':'e-Contact Added'}];
      results = sorter("contact.add", items, 'name');
      expect(results).toEqual([{'name':'contact.addition', 'display':'Contact Addition', 'atwho_order':0},
                               {'name':'econtact.added', 'display':'e-Contact Added', 'atwho_order':1}]);

    });

  });

  describe('Filter', function () {
    var data = [{"name":"contact", "display":"Contact Name"}, {"name":"channel", "display":"Channel Name"},
      {"name":"contact.age", "display":"Contact Age"}, {"name":"contact.name", "display":"Contact Name"},
      {"name":"contact.urn.path.tel.number", "display":"Contact URN tel number"},
      {"name":"channel.address", "display":"Channel Address"}
    ];

    var results, expected;

    it('should filter using findMatches', function () {
      spyOn(window, "findMatches").and.callThrough();
      results = filter("", data, 'name');
      expected = [{"name":"contact", "display":"Contact Name"}, {"name":"channel", "display":"Channel Name"}];
      expect(results).toEqual(expected);
      expect(window.findMatches).toHaveBeenCalledWith("", data, "", -1);

      results = filter("con", data, 'name');
      expected = [{"name":"contact", "display":"Contact Name"}];
      expect(results).toEqual(expected);
      expect(window.findMatches).toHaveBeenCalledWith("con", data, "", -1);

      results = filter("contact.a", data, 'name');
      expected = [{"name":"contact.age", "display":"Contact Age"}];
      expect(results).toEqual(expected);
      expect(window.findMatches).toHaveBeenCalledWith("contact.a", data, "contact", 7);

    });

  });

  describe('beforeInsert', function () {
    var value;

    it('should append a space for variables without more option', function () {
      value = beforeInsert("@new_contact");
      expect(value).toBe("@new_contact ");
    });

    it('should append a dot if value is from variables and we have more options', function () {
      value = beforeInsert('@contact', []);
      expect(value).toBe("@contact.");
    });

    it('should balance parantheses', function () {
      value = beforeInsert("@(", []);
      expect(value).toBe("@()");
    });

    it('should append parantheses for function', function () {
      value = beforeInsert("@(SUM", []);
      expect(value).toBe("@(SUM()");
    });

  });

})();
