grammar ContactQL;

// rebuild with % antlr4 -Dlanguage=Python2 ContactQL.g4 -o ../temba/contacts/search/gen -visitor -no-listener

import LexUnicode;

fragment HAS : [Hh][Aa][Ss];
fragment IS  : [Ii][Ss];

LPAREN     : '(';
RPAREN     : ')';
AND        : [Aa][Nn][Dd];
OR         : [Oo][Rr];
COMPARATOR : ('=' | '!=' | '~' | '>=' | '<=' | '>' | '<' | HAS | IS);
TEXT       : (UnicodeLetter | UnicodeDigit | '_' | '.' | '-' | '+' | '/')+;
STRING     : '"' (~["] | '""')* '"';

WS         : [ \t\n\r]+ -> skip;        // ignore whitespace

ERROR      : . ;

parse      : expression EOF;

expression : expression AND expression  # combinationAnd
           | expression expression      # combinationImpicitAnd
           | expression OR expression   # combinationOr
           | LPAREN expression RPAREN   # expressionGrouping
           | TEXT COMPARATOR literal    # condition
           | TEXT                       # implicitCondition
           ;

literal : TEXT                          # textLiteral
        | STRING                        # stringLiteral
        ;