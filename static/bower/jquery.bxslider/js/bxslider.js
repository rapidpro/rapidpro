/**
 * @file
 * Add bxslider behaviors to the page and provide Views-support.
 */

(function($) {

Drupal.behaviors.bxslider = {};
Drupal.behaviors.bxslider.attach = function(context, settings) {
  settings = settings || Drupal.settings;
alert(settings);
    if (!settings.bxslider || !settings.bxslider.sliderBx) {
    return;
  }

  $.each(settings.bxslider.sliderBx, function(key, options) {
    var $sliderBx = $(options.selector + ':not(.bxslider-processed)', context);


    if (!$sliderBx.length) {
      return;
    }

    $sliderBx.addClass('bxslider-processed').bxSlider(options);
  });
};


})(jQuery);
