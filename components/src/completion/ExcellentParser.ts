const STATE_BODY = 0; // not in a expression
const STATE_PREFIX = 1; // '@' prefix that denotes the start of an expression
const STATE_IDENTIFIER = 2; // the identifier part, e.g. 'contact.age' in '@contact.age'
const STATE_BALANCED = 3; // the balanced parentheses delimited part, e.g. '(1 + 2)' in '@(1 + 2)'
const STATE_STRING_LITERAL = 4; // a string literal which could contain )
const STATE_ESCAPED_PREFIX = 5; // a '@' prefix preceded by another '@'
const STATE_IGNORE = 6;

export interface Expression {
  start: number;
  end: number;
  text: string;
  closed: boolean;
}

/**
 * Determines whether the given string starts with the given text
 */
const startsWith = (str: string, start: string): boolean => {
  return str.indexOf(start, 0) === 0;
};

/**
 * Checks the parsed (possibly partial) expression to determine if it's valid based on how it starts
 */
const isValidStart = (
  partialExpression: string,
  allowedTopLevels: string[],
  allowIncomplete: boolean
): boolean => {
  const body = partialExpression.substring(1); // strip prefix

  if (body[0] === '(') {
    return true;
  } else {
    // if expression doesn't start with ( then check it's an allowed top level context reference
    const topLevel = body.split('.')[0].toLowerCase();

    if (allowIncomplete) {
      for (const allowed of allowedTopLevels) {
        if (startsWith(allowed, topLevel)) {
          return true;
        }
      }
    } else {
      return allowedTopLevels.indexOf(topLevel) >= 0;
    }
    return false;
  }
};

/**
 * Determines whether the given character is a word character, i.e. \w in a regex
 */
export const isWordChar = (ch: string | 0): boolean => {
  return (
    (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch === '_'
  );
};

/**
 * Determines whether we are in a string literal
 */
const isInStringLiteral = (partialExpression: string): boolean => {
  // count number quotation marks
  let numQuotes = 0;
  for (const pos of partialExpression) {
    if (pos === '"') {
      numQuotes++;
    }
  }
  return numQuotes % 2 !== 0; // odd means last string literal is open
};

export default class ExcellentParser {
  private expressionPrefix: string;
  private allowedTopLevels: string[];

  /**
   * Creates a new parser
   * @param expressionPrefix the prefix for expressions, e.g. '@'
   * @param allowedTopLevels the context names that are allowed without parentheses, e.g. ["contact", "flow", ...]
   */
  constructor(expressionPrefix: string, allowedTopLevels: string[]) {
    this.expressionPrefix = expressionPrefix;
    this.allowedTopLevels = allowedTopLevels;
  }

  /**
   * Given the text up to the caret position, returns the expression currently being edited, without its prefix
   */
  public expressionContext(textToCaret: string): string {
    const expressions = this.findExpressions(textToCaret);
    if (expressions.length === 0) {
      // no expressions found
      return null;
    }

    const lastExpression = expressions[expressions.length - 1];

    // has last expression already ended or is it closed (i.e. has balanced parentheses)
    if (lastExpression.end < textToCaret.length || lastExpression.closed) {
      return null;
    }

    return lastExpression.text.substring(1); // return without prefix
  }

  /**
   * Given the partial expression currently being edited, returns the current auto-completable identifier
   * which may be a function name or a context reference.
   */
  public autoCompleteContext(partialExpression: string): string {
    if (isInStringLiteral(partialExpression)) {
      return null;
    }

    const neededParentheses = [];
    let fragment = '';
    let skipChar = false;
    let inQuotes = false;
    let prependFlag = '';

    for (let pos = partialExpression.length - 1; pos >= 0; pos--) {
      const ch = partialExpression[pos];

      if (ch === ' ') {
        skipChar = true;
      }

      if (ch === ',') {
        skipChar = true;
        if (neededParentheses[neededParentheses.length - 1] !== '(') {
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
          if (neededParentheses[neededParentheses.length - 1] === '(') {
            neededParentheses.pop();
          }

          if (neededParentheses.length === 0) {
            skipChar = false;
          }
        }
      }

      if (ch === '(' && fragment === '') {
        prependFlag = '#';
      }

      if (skipChar || inQuotes || (ch === '(' && fragment === '')) {
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
    } else {
      return null;
    }
  }

  /**
   * TODO find the function context
   */
  public functionContext(partialExpression: string): string {
    const inString = isInStringLiteral(partialExpression);

    // initial state is string literal if number of quotes is odd
    let state = inString ? STATE_STRING_LITERAL : STATE_IGNORE;
    let identifier = '';
    let parenthesesLevel = partialExpression[-1] === '(' ? 0 : 1;

    for (let pos = partialExpression.length - 1; pos >= 0; pos--) {
      const ch = partialExpression[pos];

      if (ch === '@') {
        return '';
      }

      if (state === STATE_IGNORE) {
        if (parenthesesLevel === 0 && (isWordChar(ch) || ch === '.')) {
          state = STATE_IDENTIFIER;
          identifier = ch + identifier;
        } else if (ch === '"') {
          state = STATE_STRING_LITERAL;
        } else if (ch === '(') {
          parenthesesLevel--;
        } else if (ch === ')') {
          parenthesesLevel++;
        }
      } else if (state === STATE_IDENTIFIER) {
        if (isWordChar(ch) || ch === '.') {
          identifier = ch + identifier;
        } else {
          return identifier;
        }
      } else if (state === STATE_STRING_LITERAL) {
        if (ch === '"') {
          state = STATE_IGNORE;
        }
      }
    }
    return '';
  }

  public getContactFields(text: string): string[] {
    const fields = {};
    const re = /((parent|child\.)*contact\.)*fields\.([a-z0-9_]+)/g;
    const expressions = this.findExpressions(text);
    for (const expression of expressions) {
      let match;
      // tslint:disable-next-line:no-conditional-assignment
      while ((match = re.exec(expression.text))) {
        (fields as any)[match[3]] = true;
      }
    }
    return Object.keys(fields);
  }

  /**
   * Finds all expressions in the given text, including any partially complete expression at the end of the input
   */
  public findExpressions(text: string): Expression[] {
    const expressions: Expression[] = [];
    let state = STATE_BODY;
    let currentExpression: Expression = null;
    let parenthesesLevel = 0;

    for (let pos = 0; pos < text.length; pos++) {
      const ch = text[pos];
      // in order to determine if the b in a.b terminates an identifier, we have to peek two characters ahead as
      // it could be a.b. (b terminates) or a.b.c (b doesn't terminate)
      const nextCh = pos < text.length - 1 ? text[pos + 1] : 0;
      const nextNextCh = pos < text.length - 2 ? text[pos + 2] : 0;

      if (state === STATE_BODY) {
        if (ch === this.expressionPrefix && (isWordChar(nextCh) || nextCh === '(')) {
          state = STATE_PREFIX;
          currentExpression = {
            start: pos,
            end: null,
            text: ch,
            closed: false
          };
        } else if (ch === this.expressionPrefix && nextCh === this.expressionPrefix) {
          state = STATE_ESCAPED_PREFIX;
        }
      } else if (state === STATE_PREFIX) {
        if (isWordChar(ch)) {
          state = STATE_IDENTIFIER; // we're parsing an expression like @XXX
        } else if (ch === '(') {
          // we're parsing an expression like @(1 + 2)
          state = STATE_BALANCED;
          parenthesesLevel += 1;
        }
        currentExpression.text += ch;
      } else if (state === STATE_IDENTIFIER) {
        currentExpression.text += ch;
      } else if (state === STATE_BALANCED) {
        if (ch === '(') {
          parenthesesLevel += 1;
        } else if (ch === ')') {
          parenthesesLevel -= 1;
        } else if (ch === '"') {
          state = STATE_STRING_LITERAL;
        }

        currentExpression.text += ch;

        // expression terminates if parentheses balance
        if (parenthesesLevel === 0) {
          currentExpression.end = pos + 1;
        }
      } else if (state === STATE_STRING_LITERAL) {
        if (ch === '"') {
          state = STATE_BALANCED;
        }
        currentExpression.text += ch;
      } else if (state === STATE_ESCAPED_PREFIX) {
        state = STATE_BODY;
      }

      // identifier can terminate expression in 3 ways:
      //  1. next char is null (i.e. end of the input)
      //  2. next char is not a word character or period
      //  3. next char is a period, but it's not followed by a word character
      if (state === STATE_IDENTIFIER) {
        if (
          (!isWordChar(nextCh) && nextCh !== '.') ||
          (nextCh === '.' && !isWordChar(nextNextCh))
        ) {
          currentExpression.end = pos + 1;
        }
      }

      if (currentExpression != null && (currentExpression.end != null || nextCh === 0)) {
        const allowIncomplete = nextCh === 0; // if we're at the end of the input, allow incomplete expressions
        if (isValidStart(currentExpression.text, this.allowedTopLevels, allowIncomplete)) {
          currentExpression.closed = currentExpression.text[1] === '(' && parenthesesLevel === 0;
          currentExpression.end = pos + 1;
          expressions.push(currentExpression);
        }

        currentExpression = null;
        state = STATE_BODY;
      }
    }

    return expressions;
  }
}
