(function(excellent){var STATE_BODY=0;var STATE_PREFIX=1;var STATE_IDENTIFIER=2;var STATE_BALANCED=3;var STATE_STRING_LITERAL=4;var STATE_ESCAPED_PREFIX=5;var STATE_IGNORE=6;excellent.Parser=function(expressionPrefix,allowedTopLevels){this.expressionPrefix=expressionPrefix;this.allowedTopLevels=allowedTopLevels;};excellent.Parser.prototype.expressionContext=function(textToCaret){expressions=this.expressions(textToCaret);if(expressions.length==0){return null;}
var lastExpression=expressions[expressions.length-1];if(lastExpression.end<textToCaret.length||lastExpression.closed){return null;}
return lastExpression.text.substring(1);};excellent.Parser.prototype.autoCompleteContext=function(partialExpression){if(this.isInStringLiteral(partialExpression)){return null;}
var fragment="";var skipChar=false;var neededParentheses=[];var inQuotes=false;var prependFlag='';for(var pos=partialExpression.length-1;pos>=0;pos--){var ch=partialExpression[pos];if(ch===' '){skipChar=true;}
if(ch===','){skipChar=true;if(neededParentheses[neededParentheses.length-1]!='('){neededParentheses.push('(');}}
if(ch===')'&&!inQuotes){skipChar=true;neededParentheses.push('(');neededParentheses.push('(');}
if(ch==='"'){inQuotes=!inQuotes;}
if(skipChar){if(ch==='('&&!inQuotes){if(neededParentheses[neededParentheses.length-1]=='('){neededParentheses.pop();}
if(neededParentheses.length==0){skipChar=false;}}}
if(ch==='('&&fragment==''){prependFlag='#';}
if(skipChar||inQuotes||(ch==='('&&fragment=='')){continue;}
if(isWordChar(ch)||ch==='.'){fragment=ch+fragment;}else{break;}}
if(fragment.match(/[A-Za-z][\w]*(\.[\w]+)*/)){return prependFlag+fragment;}
else{return null;}};excellent.Parser.prototype.isInStringLiteral=function(partialExpression){var num_quotes=0;for(var pos=0;pos<partialExpression.length;pos++){if(partialExpression[pos]==='"'){num_quotes++;}}
return num_quotes%2!=0;};excellent.Parser.prototype.functionContext=function(partialExpression){var inString=this.isInStringLiteral(partialExpression);var state=inString?STATE_IGNORE:STATE_STRING_LITERAL;var identifier="";var parenthesesLevel=0;for(var pos=partialExpression.length-1;pos>=0;pos--){var ch=partialExpression[pos];if(state==STATE_IGNORE){if(parenthesesLevel==0&&(isWordChar(ch)||ch==='.')){state=STATE_IDENTIFIER;identifier=ch+identifier;}
else if(ch=="\""){state=STATE_STRING_LITERAL;}
else if(ch==='('){parenthesesLevel--;}
else if(ch===')'){parenthesesLevel++;}}
else if(state==STATE_IDENTIFIER){if(isWordChar(ch)||ch==='.'){identifier=ch+identifier;}
else{return identifier;}}
else if(state==STATE_STRING_LITERAL){if(ch=="\""){state=STATE_IGNORE;}}}
return null;};excellent.Parser.prototype.getContactFields=function(text){var fields={};var re=/(parent|child\.)*contact\.([a-z0-9_]+)/g;var expressions=this.expressions(text);for(var i=0;i<expressions.length;i++){var match;while(match=re.exec(expressions[i].text)){fields[match[2]]=true;}}
return Object.keys(fields);}
excellent.Parser.prototype.expressions=function(text){var expressions=[];var state=STATE_BODY;var currentExpression=null;var parenthesesLevel=0;for(var pos=0;pos<text.length;pos++){var ch=text[pos];var nextCh=(pos<(text.length-1))?text[pos+1]:0;var nextNextCh=(pos<(text.length-2))?text[pos+2]:0;if(state==STATE_BODY){if(ch==this.expressionPrefix&&(isWordChar(nextCh)||nextCh=='(')){state=STATE_PREFIX;currentExpression={start:pos,end:null,text:ch};}else if(ch==this.expressionPrefix&&nextCh==this.expressionPrefix){state=STATE_ESCAPED_PREFIX;}}
else if(state==STATE_PREFIX){if(isWordChar(ch)){state=STATE_IDENTIFIER;}else if(ch=='('){state=STATE_BALANCED;parenthesesLevel+=1;}
currentExpression.text+=ch;}
else if(state==STATE_IDENTIFIER){currentExpression.text+=ch;}
else if(state==STATE_BALANCED){if(ch=='('){parenthesesLevel+=1;}else if(ch==')'){parenthesesLevel-=1;}else if(ch=='"'){state=STATE_STRING_LITERAL;}
currentExpression.text+=ch;if(parenthesesLevel==0){currentExpression.end=pos+1;}}
else if(state==STATE_STRING_LITERAL){if(ch=='"'){state=STATE_BALANCED;}
currentExpression.text+=ch;}
else if(state==STATE_ESCAPED_PREFIX){state=STATE_BODY;}
if(state==STATE_IDENTIFIER){if((!isWordChar(nextCh)&&nextCh!=='.')||(nextCh==='.'&&!isWordChar(nextNextCh))){currentExpression.end=pos+1;}}
if(currentExpression!=null&&(currentExpression.end!=null||nextCh===0)){var allowIncomplete=(nextCh===0);if(isValidStart(currentExpression.text,this.allowedTopLevels,allowIncomplete)){currentExpression.closed=(currentExpression.text[1]==='(')&&(parenthesesLevel==0);currentExpression.end=pos+1;expressions.push(currentExpression);}
currentExpression=null;state=STATE_BODY;}}
return expressions;};function isValidStart(partialExpression,allowedTopLevels,allowIncomplete){var body=partialExpression.substring(1);if(body[0]==='('){return true;}else{var topLevel=body.split('.')[0].toLowerCase();if(allowIncomplete){for(var n=0;n<allowedTopLevels.length;n++){if(startsWith(allowedTopLevels[n],topLevel)){return true;}}}else{return allowedTopLevels.indexOf(topLevel)>=0;}
return false;}}
function startsWith(str,start){return str.indexOf(start,0)===0;}
function isWordChar(ch){return(ch>='a'&&ch<='z')||(ch>='A'&&ch<='Z')||(ch>='0'&&ch<='9')||ch=='_';}}(window.excellent=window.excellent||{}));