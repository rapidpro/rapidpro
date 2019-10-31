# Generated from ContactQL.g4 by ANTLR 4.7.2
from antlr4 import *

if __name__ is not None and "." in __name__:
    from .ContactQLParser import ContactQLParser
else:
    from ContactQLParser import ContactQLParser

# This class defines a complete generic visitor for a parse tree produced by ContactQLParser.


class ContactQLVisitor(ParseTreeVisitor):

    # Visit a parse tree produced by ContactQLParser#parse.
    def visitParse(self, ctx: ContactQLParser.ParseContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by ContactQLParser#implicitCondition.
    def visitImplicitCondition(self, ctx: ContactQLParser.ImplicitConditionContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by ContactQLParser#condition.
    def visitCondition(self, ctx: ContactQLParser.ConditionContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by ContactQLParser#combinationAnd.
    def visitCombinationAnd(self, ctx: ContactQLParser.CombinationAndContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by ContactQLParser#combinationImpicitAnd.
    def visitCombinationImpicitAnd(self, ctx: ContactQLParser.CombinationImpicitAndContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by ContactQLParser#combinationOr.
    def visitCombinationOr(self, ctx: ContactQLParser.CombinationOrContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by ContactQLParser#expressionGrouping.
    def visitExpressionGrouping(self, ctx: ContactQLParser.ExpressionGroupingContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by ContactQLParser#textLiteral.
    def visitTextLiteral(self, ctx: ContactQLParser.TextLiteralContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by ContactQLParser#stringLiteral.
    def visitStringLiteral(self, ctx: ContactQLParser.StringLiteralContext):
        return self.visitChildren(ctx)


del ContactQLParser
