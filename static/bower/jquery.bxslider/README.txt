
Description
-----------
This module provides a central function for adding bxslider jQuery plugin
elements. For more information about bxslider, visit the official project:
http://bxslider.com/


Installation
------------
1) Place this module directory in your modules folder (this will usually be
   "sites/all/modules/").

2) Enable the module within your Drupal site at Administer -> Site Building ->
   Modules (admin/build/modules).

Usage
-----
The bxslider module is most commonly used with the Views module to turn
listings of images or other content into a carousel.

1) Install the Views module (http://drupal.org/project/views) on your Drupal
   site if you have not already.

2) Add a new view at Administration -> Structure -> Views (admin/structure/views).

3) Change the "Display format" of the view to "bxslider". Disable the
   "Use pager" option, which cannot be used with the bxslider style. Click the
   "Continue & Edit" button to configure the rest of the View.

4) Click on the "Settings" link next to the bxslider Format to configure the
   options for the carousel such as the animation speed and skin.

5) Add the items you would like to include in the rotator under the "Fields"
   section, and build out the rest of the view as you would normally. Note that
   the preview of the carousel within Views probably will not appear correctly
   because the necessary JavaScript and CSS is not loaded in the Views
   interface. Save your view and visit a page URL containing the view to see
   how it appears.
