# Generated from ContactQL.g4 by ANTLR 4.7.2
# encoding: utf-8
import sys
from io import StringIO
from typing.io import TextIO

from antlr4 import *


def serializedATN():
    with StringIO() as buf:
        buf.write("\3\u608b\ua72a\u8133\ub9ed\u417c\u3be7\u7786\u5964\3\13")
        buf.write("(\4\2\t\2\4\3\t\3\4\4\t\4\3\2\3\2\3\2\3\3\3\3\3\3\3\3")
        buf.write("\3\3\3\3\3\3\3\3\3\3\5\3\25\n\3\3\3\3\3\3\3\3\3\3\3\3")
        buf.write('\3\3\3\3\3\7\3\37\n\3\f\3\16\3"\13\3\3\4\3\4\5\4&\n\4')
        buf.write("\3\4\2\3\4\5\2\4\6\2\2\2*\2\b\3\2\2\2\4\24\3\2\2\2\6%")
        buf.write("\3\2\2\2\b\t\5\4\3\2\t\n\7\2\2\3\n\3\3\2\2\2\13\f\b\3")
        buf.write("\1\2\f\r\7\3\2\2\r\16\5\4\3\2\16\17\7\4\2\2\17\25\3\2")
        buf.write("\2\2\20\21\7\b\2\2\21\22\7\7\2\2\22\25\5\6\4\2\23\25\7")
        buf.write("\b\2\2\24\13\3\2\2\2\24\20\3\2\2\2\24\23\3\2\2\2\25 \3")
        buf.write("\2\2\2\26\27\f\b\2\2\27\30\7\5\2\2\30\37\5\4\3\t\31\32")
        buf.write("\f\7\2\2\32\37\5\4\3\b\33\34\f\6\2\2\34\35\7\6\2\2\35")
        buf.write("\37\5\4\3\7\36\26\3\2\2\2\36\31\3\2\2\2\36\33\3\2\2\2")
        buf.write('\37"\3\2\2\2 \36\3\2\2\2 !\3\2\2\2!\5\3\2\2\2" \3\2')
        buf.write("\2\2#&\7\b\2\2$&\7\t\2\2%#\3\2\2\2%$\3\2\2\2&\7\3\2\2")
        buf.write("\2\6\24\36 %")
        return buf.getvalue()


class ContactQLParser(Parser):

    grammarFileName = "ContactQL.g4"

    atn = ATNDeserializer().deserialize(serializedATN())

    decisionsToDFA = [DFA(ds, i) for i, ds in enumerate(atn.decisionToState)]

    sharedContextCache = PredictionContextCache()

    literalNames = ["<INVALID>", "'('", "')'"]

    symbolicNames = ["<INVALID>", "LPAREN", "RPAREN", "AND", "OR", "COMPARATOR", "TEXT", "STRING", "WS", "ERROR"]

    RULE_parse = 0
    RULE_expression = 1
    RULE_literal = 2

    ruleNames = ["parse", "expression", "literal"]

    EOF = Token.EOF
    LPAREN = 1
    RPAREN = 2
    AND = 3
    OR = 4
    COMPARATOR = 5
    TEXT = 6
    STRING = 7
    WS = 8
    ERROR = 9

    def __init__(self, input: TokenStream, output: TextIO = sys.stdout):
        super().__init__(input, output)
        self.checkVersion("4.7.2")
        self._interp = ParserATNSimulator(self, self.atn, self.decisionsToDFA, self.sharedContextCache)
        self._predicates = None

    class ParseContext(ParserRuleContext):
        def __init__(self, parser, parent: ParserRuleContext = None, invokingState: int = -1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def expression(self):
            return self.getTypedRuleContext(ContactQLParser.ExpressionContext, 0)

        def EOF(self):
            return self.getToken(ContactQLParser.EOF, 0)

        def getRuleIndex(self):
            return ContactQLParser.RULE_parse

        def accept(self, visitor: ParseTreeVisitor):
            if hasattr(visitor, "visitParse"):
                return visitor.visitParse(self)
            else:
                return visitor.visitChildren(self)

    def parse(self):

        localctx = ContactQLParser.ParseContext(self, self._ctx, self.state)
        self.enterRule(localctx, 0, self.RULE_parse)
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 6
            self.expression(0)
            self.state = 7
            self.match(ContactQLParser.EOF)
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx

    class ExpressionContext(ParserRuleContext):
        def __init__(self, parser, parent: ParserRuleContext = None, invokingState: int = -1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def getRuleIndex(self):
            return ContactQLParser.RULE_expression

        def copyFrom(self, ctx: ParserRuleContext):
            super().copyFrom(ctx)

    class ImplicitConditionContext(ExpressionContext):
        def __init__(self, parser, ctx: ParserRuleContext):  # actually a ContactQLParser.ExpressionContext
            super().__init__(parser)
            self.copyFrom(ctx)

        def TEXT(self):
            return self.getToken(ContactQLParser.TEXT, 0)

        def accept(self, visitor: ParseTreeVisitor):
            if hasattr(visitor, "visitImplicitCondition"):
                return visitor.visitImplicitCondition(self)
            else:
                return visitor.visitChildren(self)

    class ConditionContext(ExpressionContext):
        def __init__(self, parser, ctx: ParserRuleContext):  # actually a ContactQLParser.ExpressionContext
            super().__init__(parser)
            self.copyFrom(ctx)

        def TEXT(self):
            return self.getToken(ContactQLParser.TEXT, 0)

        def COMPARATOR(self):
            return self.getToken(ContactQLParser.COMPARATOR, 0)

        def literal(self):
            return self.getTypedRuleContext(ContactQLParser.LiteralContext, 0)

        def accept(self, visitor: ParseTreeVisitor):
            if hasattr(visitor, "visitCondition"):
                return visitor.visitCondition(self)
            else:
                return visitor.visitChildren(self)

    class CombinationAndContext(ExpressionContext):
        def __init__(self, parser, ctx: ParserRuleContext):  # actually a ContactQLParser.ExpressionContext
            super().__init__(parser)
            self.copyFrom(ctx)

        def expression(self, i: int = None):
            if i is None:
                return self.getTypedRuleContexts(ContactQLParser.ExpressionContext)
            else:
                return self.getTypedRuleContext(ContactQLParser.ExpressionContext, i)

        def AND(self):
            return self.getToken(ContactQLParser.AND, 0)

        def accept(self, visitor: ParseTreeVisitor):
            if hasattr(visitor, "visitCombinationAnd"):
                return visitor.visitCombinationAnd(self)
            else:
                return visitor.visitChildren(self)

    class CombinationImpicitAndContext(ExpressionContext):
        def __init__(self, parser, ctx: ParserRuleContext):  # actually a ContactQLParser.ExpressionContext
            super().__init__(parser)
            self.copyFrom(ctx)

        def expression(self, i: int = None):
            if i is None:
                return self.getTypedRuleContexts(ContactQLParser.ExpressionContext)
            else:
                return self.getTypedRuleContext(ContactQLParser.ExpressionContext, i)

        def accept(self, visitor: ParseTreeVisitor):
            if hasattr(visitor, "visitCombinationImpicitAnd"):
                return visitor.visitCombinationImpicitAnd(self)
            else:
                return visitor.visitChildren(self)

    class CombinationOrContext(ExpressionContext):
        def __init__(self, parser, ctx: ParserRuleContext):  # actually a ContactQLParser.ExpressionContext
            super().__init__(parser)
            self.copyFrom(ctx)

        def expression(self, i: int = None):
            if i is None:
                return self.getTypedRuleContexts(ContactQLParser.ExpressionContext)
            else:
                return self.getTypedRuleContext(ContactQLParser.ExpressionContext, i)

        def OR(self):
            return self.getToken(ContactQLParser.OR, 0)

        def accept(self, visitor: ParseTreeVisitor):
            if hasattr(visitor, "visitCombinationOr"):
                return visitor.visitCombinationOr(self)
            else:
                return visitor.visitChildren(self)

    class ExpressionGroupingContext(ExpressionContext):
        def __init__(self, parser, ctx: ParserRuleContext):  # actually a ContactQLParser.ExpressionContext
            super().__init__(parser)
            self.copyFrom(ctx)

        def LPAREN(self):
            return self.getToken(ContactQLParser.LPAREN, 0)

        def expression(self):
            return self.getTypedRuleContext(ContactQLParser.ExpressionContext, 0)

        def RPAREN(self):
            return self.getToken(ContactQLParser.RPAREN, 0)

        def accept(self, visitor: ParseTreeVisitor):
            if hasattr(visitor, "visitExpressionGrouping"):
                return visitor.visitExpressionGrouping(self)
            else:
                return visitor.visitChildren(self)

    def expression(self, _p: int = 0):
        _parentctx = self._ctx
        _parentState = self.state
        localctx = ContactQLParser.ExpressionContext(self, self._ctx, _parentState)
        _prevctx = localctx
        _startState = 2
        self.enterRecursionRule(localctx, 2, self.RULE_expression, _p)
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 18
            self._errHandler.sync(self)
            la_ = self._interp.adaptivePredict(self._input, 0, self._ctx)
            if la_ == 1:
                localctx = ContactQLParser.ExpressionGroupingContext(self, localctx)
                self._ctx = localctx
                _prevctx = localctx

                self.state = 10
                self.match(ContactQLParser.LPAREN)
                self.state = 11
                self.expression(0)
                self.state = 12
                self.match(ContactQLParser.RPAREN)
                pass

            elif la_ == 2:
                localctx = ContactQLParser.ConditionContext(self, localctx)
                self._ctx = localctx
                _prevctx = localctx
                self.state = 14
                self.match(ContactQLParser.TEXT)
                self.state = 15
                self.match(ContactQLParser.COMPARATOR)
                self.state = 16
                self.literal()
                pass

            elif la_ == 3:
                localctx = ContactQLParser.ImplicitConditionContext(self, localctx)
                self._ctx = localctx
                _prevctx = localctx
                self.state = 17
                self.match(ContactQLParser.TEXT)
                pass

            self._ctx.stop = self._input.LT(-1)
            self.state = 30
            self._errHandler.sync(self)
            _alt = self._interp.adaptivePredict(self._input, 2, self._ctx)
            while _alt != 2 and _alt != ATN.INVALID_ALT_NUMBER:
                if _alt == 1:
                    if self._parseListeners is not None:
                        self.triggerExitRuleEvent()
                    _prevctx = localctx
                    self.state = 28
                    self._errHandler.sync(self)
                    la_ = self._interp.adaptivePredict(self._input, 1, self._ctx)
                    if la_ == 1:
                        localctx = ContactQLParser.CombinationAndContext(
                            self, ContactQLParser.ExpressionContext(self, _parentctx, _parentState)
                        )
                        self.pushNewRecursionContext(localctx, _startState, self.RULE_expression)
                        self.state = 20
                        if not self.precpred(self._ctx, 6):
                            from antlr4.error.Errors import FailedPredicateException

                            raise FailedPredicateException(self, "self.precpred(self._ctx, 6)")
                        self.state = 21
                        self.match(ContactQLParser.AND)
                        self.state = 22
                        self.expression(7)
                        pass

                    elif la_ == 2:
                        localctx = ContactQLParser.CombinationImpicitAndContext(
                            self, ContactQLParser.ExpressionContext(self, _parentctx, _parentState)
                        )
                        self.pushNewRecursionContext(localctx, _startState, self.RULE_expression)
                        self.state = 23
                        if not self.precpred(self._ctx, 5):
                            from antlr4.error.Errors import FailedPredicateException

                            raise FailedPredicateException(self, "self.precpred(self._ctx, 5)")
                        self.state = 24
                        self.expression(6)
                        pass

                    elif la_ == 3:
                        localctx = ContactQLParser.CombinationOrContext(
                            self, ContactQLParser.ExpressionContext(self, _parentctx, _parentState)
                        )
                        self.pushNewRecursionContext(localctx, _startState, self.RULE_expression)
                        self.state = 25
                        if not self.precpred(self._ctx, 4):
                            from antlr4.error.Errors import FailedPredicateException

                            raise FailedPredicateException(self, "self.precpred(self._ctx, 4)")
                        self.state = 26
                        self.match(ContactQLParser.OR)
                        self.state = 27
                        self.expression(5)
                        pass

                self.state = 32
                self._errHandler.sync(self)
                _alt = self._interp.adaptivePredict(self._input, 2, self._ctx)

        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.unrollRecursionContexts(_parentctx)
        return localctx

    class LiteralContext(ParserRuleContext):
        def __init__(self, parser, parent: ParserRuleContext = None, invokingState: int = -1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def getRuleIndex(self):
            return ContactQLParser.RULE_literal

        def copyFrom(self, ctx: ParserRuleContext):
            super().copyFrom(ctx)

    class StringLiteralContext(LiteralContext):
        def __init__(self, parser, ctx: ParserRuleContext):  # actually a ContactQLParser.LiteralContext
            super().__init__(parser)
            self.copyFrom(ctx)

        def STRING(self):
            return self.getToken(ContactQLParser.STRING, 0)

        def accept(self, visitor: ParseTreeVisitor):
            if hasattr(visitor, "visitStringLiteral"):
                return visitor.visitStringLiteral(self)
            else:
                return visitor.visitChildren(self)

    class TextLiteralContext(LiteralContext):
        def __init__(self, parser, ctx: ParserRuleContext):  # actually a ContactQLParser.LiteralContext
            super().__init__(parser)
            self.copyFrom(ctx)

        def TEXT(self):
            return self.getToken(ContactQLParser.TEXT, 0)

        def accept(self, visitor: ParseTreeVisitor):
            if hasattr(visitor, "visitTextLiteral"):
                return visitor.visitTextLiteral(self)
            else:
                return visitor.visitChildren(self)

    def literal(self):

        localctx = ContactQLParser.LiteralContext(self, self._ctx, self.state)
        self.enterRule(localctx, 4, self.RULE_literal)
        try:
            self.state = 35
            self._errHandler.sync(self)
            token = self._input.LA(1)
            if token in [ContactQLParser.TEXT]:
                localctx = ContactQLParser.TextLiteralContext(self, localctx)
                self.enterOuterAlt(localctx, 1)
                self.state = 33
                self.match(ContactQLParser.TEXT)
                pass
            elif token in [ContactQLParser.STRING]:
                localctx = ContactQLParser.StringLiteralContext(self, localctx)
                self.enterOuterAlt(localctx, 2)
                self.state = 34
                self.match(ContactQLParser.STRING)
                pass
            else:
                raise NoViableAltException(self)

        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx

    def sempred(self, localctx: RuleContext, ruleIndex: int, predIndex: int):
        if self._predicates == None:
            self._predicates = dict()
        self._predicates[1] = self.expression_sempred
        pred = self._predicates.get(ruleIndex, None)
        if pred is None:
            raise Exception("No predicate with index:" + str(ruleIndex))
        else:
            return pred(localctx, predIndex)

    def expression_sempred(self, localctx: ExpressionContext, predIndex: int):
        if predIndex == 0:
            return self.precpred(self._ctx, 6)

        if predIndex == 1:
            return self.precpred(self._ctx, 5)

        if predIndex == 2:
            return self.precpred(self._ctx, 4)
