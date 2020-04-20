----
/**
 * This file is part of jQuery History
 * Copyright (C) 2008-2010 Benjamin Arthur Lupton
 * http://www.balupton.com/projects/jquery-history
 *
 * jQuery History is free software; You can redistribute it and/or modify it under the terms of
 * the GNU Affero General Public License version 3 as published by the Free Software Foundation.
 * You don't have to do anything special to accept the license and you don’t have to notify
 * anyone which that you have made that decision.
 * 
 * jQuery History is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
 * without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See your chosen license for more details.
 * 
 * You should have received along with jQuery History:
 * - A copy of the license used.
 *   If not, see <http://www.gnu.org/licenses/agpl-3.0.html>.
 * - A copy of our interpretation of the license used.
 *   If not, see <http://github.com/balupton/jquery-history/blob/master/COPYING.txt>.
 * 
 * @version 1.5.0-final
 * @date August 31, 2010
 * @since v0.1.0-dev, July 24, 2008
 * @category jquery-plugin
 * @package jquery-history {@link http://www.balupton/projects/jquery-history}
 * @author Benjamin "balupton" Lupton {@link http://www.balupton.com}
 * @copyright (c) 2008-2010 Benjamin Arthur Lupton {@link http://www.balupton.com}
 * @license GNU Affero General Public License version 3 {@link http://www.gnu.org/licenses/agpl-3.0.html}
 * @example Visit {@link http://www.balupton.com/projects/jquery-history} for more information.
 */
----

Installation & Usage:
1. Refer to the (demo/index.html) or http://www.balupton.com/projects/jquery-history if the demo is not included.

Todo:
1. Fix known issues if there are any.

Known Issues:
1. None. Yay!


----

Query Strings:

If you would like to have a QueryString in your hash and fetch the contents of it. So for example we have:
	http://localhost/page/#subpage?a=true&b=false

And we would like to extract b. Then we can do:
	var hashData = hash.queryStringToJSON();
	console.log(hashData); // {a:true,b:false}
	console.log(hashData.a); // true
	console.log(hashData.b); // false

But first, you will have to download the queryStringToJSON function from within here:
	http://github.com/balupton/jquery-sparkle/blob/master/scripts/resources/core.string.js

And place it within your own code.
It is not included within jQuery History by default, as it is not essential.


----

Changelog:

v1.5.0-final, August 31, 2010
- Removed core.string.js and jquery.extra.js as they were not needed. Fixes issue with autocomplete.
- Updated jQuery Sparkle dependencies to [v1.5.1-beta, August 31, 2010]

v1.4.4-final, August 21, 2010
- Updated jQuery Sparkle dependencies to [v1.4.17-final, August 21, 2010]

v1.4.4-final, August 21, 2010
- Updated jQuery Sparkle dependencies to [v1.4.17-final, August 21, 2010]

v1.4.3-final, August 19, 2010
- Improved installation instructions to make more clear.
- Updated Syntax Highlighter include and initialisation. We use http://www.balupton.com/projects/jquery-syntaxhighlighter
- Code blocks within the demo are now using PRE instead of CODE elements due to an IE bug.
- Updated jQuery Sparkle dependencies to [v1.4.14-final, August 19, 2010]

v1.4.2-final, August 12, 2010
- Now recognises the new wave of browsers which also have native support for the onhashchange event.
- This is a recommended update for all users. It now brings the final status, and can be considered of production quality.

v1.4.1-beta, August 05, 2010
- Removed the extractAnchor and extractState as they are ambiguous and are better placed in the jQuery Ajaxy project.

v1.4.0-beta, August 03, 2010
- Renamed format to extractHash, added extractAnchor and extractState as well. [Backwards Compatibility Break]

v1.3.0-beta, August 01, 2010
- Updated licensing information. Still using the same license, as it is the best there is, but just provided some more information on it to make life simpler.
- Updated jQuery Sparkle dependencies to [v1.4.8-beta, August 01, 2010]
- Added jQuery Ajaxy references into demo page

v1.2.0-dev, July 21, 2010
- New demo, moved to github, split from Ajaxy, added Makefile

v1.1.0-final, July 14, 2009
- Rewrote IE<8 hash code
- Cut down format to accept all hash types

v1.0.1-final, July 11, 2009
- Restructured a little bit
- Documented
- Cleaned go/request

v1.0.0-final, June 19, 2009
- Been stable for over a year now, pushing live.

v0.1.0-dev, July 24, 2008
- Initial Release

----

Special Thanks:
- jQuery {@link http://jquery.com/}
- jQuery UI History - Klaus Hartl {@link http://www.stilbuero.de/jquery/ui_history/}
- Really Simple History - Brian Dillard and Brad Neuberg {@link http://code.google.com/p/reallysimplehistory/}
- jQuery History Plugin - Taku Sano (Mikage Sawatari) {@link http://www.mikage.to/jquery/jquery_history.html}
- jQuery History Remote Plugin - Klaus Hartl {@link http://stilbuero.de/jquery/history/}
- Content With Style: Fixing the back button and enabling bookmarking for ajax apps - Mike Stenhouse {@link http://www.contentwithstyle.co.uk/Articles/38/fixing-the-back-button-and-enabling-bookmarking-for-ajax-apps}
- Bookmarks and Back Buttons {@link http://ajax.howtosetup.info/options-and-efficiencies/bookmarks-and-back-buttons/}
- Ajax: How to handle bookmarks and back buttons - Brad Neuberg {@link http://dev.aol.com/ajax-handling-bookmarks-and-back-button}
