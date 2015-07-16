(function () {
  'use strict';

  describe('matcher tests', function () {
    var matched;

    it('should match "" after flag', function () {
      matched = matcher("@", "some texts before @", "@");
      expect(matched).toBe("");
    });

    it('should not match escaped texts', function () {
      matched = matcher("@", "some texts before @@contact", "@");
      expect(matched).toBe(null);
    });

    it('should match variable after flag', function () {
      matched = matcher("@", "some texts before @contact", "@");
      expect(matched).toBe("contact");
    });

    it('should match variables with dot', function () {
      matched = matcher("@", "some texts before @contact.born", "@");
      expect(matched).toBe("contact.born");
    });

    it('should match variables with dot as big as possible', function () {
      matched = matcher("@", "some texts before @contact.born.where.location", "@");
      expect(matched).toBe("contact.born.where.location");
    });

    it('should not match space if we have a space at the end', function () {
      matched = matcher("@", "some texts before @contact ", "@");
      expect(matched).toBe(null);
    });

    it('should not match space if if last word does not have flag', function () {
      matched = matcher("@", "some texts before @contact contact", "@");
      expect(matched).toBe(null);
    });

    it('should match functions', function () {
      matched = matcher("@", "some texts before @(SUM", "@");
      expect(matched).toBe("(SUM");
    });

    it('should not match escaped functions', function () {
      matched = matcher("@", "some texts before @@(SUM", "@");
      expect(matched).toBe(null);
    });

    it('should match all the function', function () {
      matched = matcher("@", "some texts before @(SUM()", "@");
      expect(matched).toBe("(SUM()");
    });

    it('should match the function as long as possible', function () {
      matched = matcher("@", "some texts before @(SUM(contact.age, step.value", "@");
      expect(matched).toBe("(SUM(contact.age, step.value");
    });

    it('should match the function as long as possible', function () {
      matched = matcher("@", "some texts before @(SUM(contact.age, step.value))))", "@");
      expect(matched).toBe("(SUM(contact.age, step.value))))");
    });

    it('should not match if space after last )', function () {
      matched = matcher("@", "some texts before @(SUM(contact.age, step.value)))) ", "@");
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
  });


})();
