/**
 * Javascript parser for Excellent-style expressions used in RapidPro
 */
(function(excellent) {

    var STATE_BODY = 0;               // not in a expression
    var STATE_PREFIX = 1;             // '@' prefix that denotes the start of an expression
    var STATE_IDENTIFIER = 2;         // the identifier part, e.g. 'contact.age' in '@contact.age'
    var STATE_BALANCED = 3;           // the balanced parentheses delimited part, e.g. '(1 + 2)' in '@(1 + 2)'
    var STATE_STRING_LITERAL = 4;     // a string literal which could contain )
    var STATE_ESCAPED_PREFIX = 5;     // a '@' prefix preceded by another '@'
    var STATE_IGNORE = 6;

    /**
     * Creates a new parser
     * @param expressionPrefix the prefix for expressions, e.g. '@'
     * @param allowedTopLevels the context names that are allowed without parentheses, e.g. ["contact", "flow", ...]
     */
    excellent.Parser = function(expressionPrefix, allowedTopLevels) {
        this.expressionPrefix = expressionPrefix;
        this.allowedTopLevels = allowedTopLevels;
    };

    /**
     * Given the text up to the caret position, returns the expression currently being edited, without its prefix
     */
    excellent.Parser.prototype.expressionContext = function(textToCaret) {
        expressions = this.expressions(textToCaret);

        if (expressions.length == 0) { // no expressions found
            return null;
        }

        var lastExpression = expressions[expressions.length - 1];

        // has last expression already ended or is it closed (i.e. has balanced parentheses)
        if (lastExpression.end < textToCaret.length || lastExpression.closed) {
            return null;
        }

        return lastExpression.text.substring(1);  // return without prefix
    };

    /**
     * Given the partial expression currently being edited, returns the current auto-completable identifier
     * which may be a function name or a context reference.
     */
    excellent.Parser.prototype.autoCompleteContext = function(partialExpression) {
        if (this.isInStringLiteral(partialExpression)) {
            return null;
        }

        var fragment = "";
        var skipChar = false;
        var neededParentheses = [];
        var inQuotes = false;
        var prependFlag = '';

        for (var pos = partialExpression.length - 1; pos >= 0; pos--) {
            var ch = partialExpression[pos];

            if (ch === ' ') {
                skipChar = true;
            }

            if (ch === ',') {
                skipChar = true;
                if (neededParentheses[neededParentheses.length - 1] != '(') {
                    neededParentheses.push('(');
                }
            }

            if (ch === ')' && !inQuotes) {
                skipChar = true;
                neededParentheses.push('(');
                neededParentheses.push('(');
            }

            if (ch === '"') {
                inQuotes = !inQuotes;
            }

            if (skipChar) {
                if (ch === '(' && !inQuotes) {
                    if (neededParentheses[neededParentheses.length - 1] == '(') {
                        neededParentheses.pop();
                    }

                    if (neededParentheses.length == 0) {
                        skipChar = false;
                    }
                }
            }

            if (ch === '(' && fragment == '') {
                prependFlag = '#';
            }

            if (skipChar || inQuotes || (ch === '(' && fragment == '')) {
                continue;
            }

            if (isWordChar(ch) || ch === '.') {
                fragment = ch + fragment;
            } else {
                break;
            }
        }

        if (fragment.match(/[A-Za-z][\w]*(\.[\w]+)*/)) {
            return prependFlag + fragment;
        }
        else {
            return null;
        }
    };

    /**
     * Determines whether we are in a string literal
     */
    excellent.Parser.prototype.isInStringLiteral = function(partialExpression) {
        // count number quotation marks
        var num_quotes = 0;
        for (var pos = 0; pos < partialExpression.length; pos++) {
            if (partialExpression[pos] === '"') {
                num_quotes++;
            }
        }
        return num_quotes % 2 != 0;  // odd means last string literal is open
    };


    /**
     * TODO find the function context
     */
    excellent.Parser.prototype.functionContext = function(partialExpression) {
        var inString = this.isInStringLiteral(partialExpression);

        // initial state is string literal if number of quotes is odd
        var state = inString ? STATE_IGNORE : STATE_STRING_LITERAL;
        var identifier = "";
        var parenthesesLevel = 0;

        for (var pos = partialExpression.length - 1; pos >= 0; pos--) {
            var ch = partialExpression[pos];

            if (state == STATE_IGNORE) {
                if (parenthesesLevel == 0 && (isWordChar(ch) || ch === '.')) {
                    state = STATE_IDENTIFIER;
                    identifier = ch + identifier;
                }
                else if (ch == "\"") {
                    state = STATE_STRING_LITERAL;
                }
                else if (ch === '(') {
                    parenthesesLevel--;
                }
                else if (ch === ')') {
                    parenthesesLevel++;
                }
            }
            else if (state == STATE_IDENTIFIER) {
                if (isWordChar(ch) || ch === '.') {
                    identifier = ch + identifier;
                }
                else {
                    return identifier;
                }
            }
            else if (state == STATE_STRING_LITERAL) {
                if (ch == "\"") {
                    state = STATE_IGNORE;
                }
            }
        }
        return null;
    };

    excellent.Parser.prototype.getContactFields = function(text) {
        var fields = {};
        var re = /(parent|child\.)*contact\.([a-z0-9_]+)/g;
        var expressions = this.expressions(text);
        for (var i=0; i<expressions.length; i++) {
            var match;
            while (match = re.exec(expressions[i].text)) {
                fields[match[2]] = true;
            }
        }
        return Object.keys(fields);
    }

    /**
     * Finds all expressions in the given text, including any partially complete expression at the end of the input
     */
    excellent.Parser.prototype.expressions = function(text) {
        var expressions = [];
        var state = STATE_BODY;
        var currentExpression = null;
        var parenthesesLevel = 0;

        for (var pos = 0; pos < text.length; pos++) {
            var ch = text[pos];

            // in order to determine if the b in a.b terminates an identifier, we have to peek two characters ahead as
            // it could be a.b. (b terminates) or a.b.c (b doesn't terminate)
            var nextCh = (pos < (text.length - 1)) ? text[pos + 1] : 0;
            var nextNextCh = (pos < (text.length - 2)) ? text[pos + 2] : 0;

            if (state == STATE_BODY) {
                if (ch == this.expressionPrefix && (isWordChar(nextCh) || nextCh == '(')) {
                    state = STATE_PREFIX;
                    currentExpression = {start: pos, end: null, text: ch};
                } else if (ch == this.expressionPrefix && nextCh == this.expressionPrefix) {
                    state = STATE_ESCAPED_PREFIX;
                }
            }
            else if (state == STATE_PREFIX) {
                if (isWordChar(ch)) {
                    state = STATE_IDENTIFIER; // we're parsing an expression like @XXX
                } else if (ch == '(') {
                    // we're parsing an expression like @(1 + 2)
                    state = STATE_BALANCED;
                    parenthesesLevel += 1;
                }
                currentExpression.text += ch;
            }
            else if (state == STATE_IDENTIFIER) {
                currentExpression.text += ch;
            }
            else if (state == STATE_BALANCED) {
                if (ch == '(') {
                    parenthesesLevel += 1;
                } else if (ch == ')') {
                    parenthesesLevel -= 1;
                } else if (ch == '"') {
                    state = STATE_STRING_LITERAL;
                }

                currentExpression.text += ch;

                // expression terminates if parentheses balance
                if (parenthesesLevel == 0) {
                    currentExpression.end = pos + 1;
                }
            }
            else if (state == STATE_STRING_LITERAL) {
                if (ch == '"') {
                    state = STATE_BALANCED;
                }
                currentExpression.text += ch;
            }
            else if (state == STATE_ESCAPED_PREFIX) {
                state = STATE_BODY;
            }

            // identifier can terminate expression in 3 ways:
            //  1. next char is null (i.e. end of the input)
            //  2. next char is not a word character or period
            //  3. next char is a period, but it's not followed by a word character
            if (state == STATE_IDENTIFIER) {
                if ((!isWordChar(nextCh) && nextCh !== '.') || (nextCh === '.' && !isWordChar(nextNextCh))) {
                    currentExpression.end = pos + 1;
                }
            }

            if (currentExpression != null && (currentExpression.end != null || nextCh === 0)) {
                var allowIncomplete = (nextCh === 0); // if we're at the end of the input, allow incomplete expressions

                if (isValidStart(currentExpression.text, this.allowedTopLevels, allowIncomplete)) {
                	currentExpression.closed = (currentExpression.text[1] === '(') && (parenthesesLevel == 0);
                    currentExpression.end = pos + 1;
                    expressions.push(currentExpression);
                }

                currentExpression = null;
                state = STATE_BODY;
            }
        }

        return expressions;
    };

    /**
     * Checks the parsed (possibly partial) expression to determine if it's valid based on how it starts
     */
    function isValidStart(partialExpression, allowedTopLevels, allowIncomplete) {
        var body = partialExpression.substring(1); // strip prefix

        if (body[0] === '(') {
            return true;
        } else {
            // if expression doesn't start with ( then check it's an allowed top level context reference
            var topLevel = body.split('.')[0].toLowerCase();

            if (allowIncomplete) {
                for (var n = 0; n < allowedTopLevels.length; n++) {
                    if (startsWith(allowedTopLevels[n], topLevel)) {
                        return true;
                    }
                }
            } else {
                return allowedTopLevels.indexOf(topLevel) >= 0;
            }
            return false;
        }
    }

    /**
     * Determines whether the given string starts with the given text
     */
    function startsWith(str, start) {
        return str.indexOf(start, 0) === 0;
    }

    /**
     * Determines whether the given character is a word character, i.e. \w in a regex
     */
    function isWordChar(ch) {
        return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '_';
    }

}(window.excellent = window.excellent || {}));
