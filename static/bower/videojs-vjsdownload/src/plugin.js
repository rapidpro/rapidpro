import videojs from 'video.js';

// Default options for the plugin.
const defaults = {
  beforeElement: 'fullscreenToggle',
  textControl: 'Download video',
  name: 'downloadButton'
};

const vjsButton = videojs.getComponent('Button');

class DownloadButton extends vjsButton {

  /**
  * Allow sub components to stack CSS class names
  *
  * @return {String} The constructed class name
  * @method buildCSSClass
  */
  buildCSSClass() {
    return `vjs-vjsdownload ${super.buildCSSClass()}`;
  }

  /**
  * Handles click for full screen
  *
  * @method handleClick
  */
  handleClick() {
    let p = this.player();

    window.open(p.currentSrc(), 'Download');
    p.trigger('downloadvideo');
  }

}

/**
 * Function to invoke when the player is ready.
 *
 * This is a great place for your plugin to initialize itself. When this
 * function is called, the player will have its DOM and child components
 * in place.
 *
 * @function onPlayerReady
 * @param    {Player} player
 * @param    {Object} [options={}]
 */
const onPlayerReady = (player, options) => {
  let DButton = player.controlBar.addChild(new DownloadButton(player, options), {});

  DButton.controlText(options.textControl);

  player.controlBar.el().insertBefore(DButton.el(),
    player.controlBar.getChild(options.beforeElement).el());

  player.addClass('vjs-vjsdownload');
};

/**
 * A video.js plugin.
 *
 * In the plugin function, the value of `this` is a video.js `Player`
 * instance. You cannot rely on the player being in a "ready" state here,
 * depending on how the plugin is invoked. This may or may not be important
 * to you; if not, remove the wait for "ready"!
 *
 * @function vjsdownload
 * @param    {Object} [options={}]
 *           An object of options left to the plugin author to define.
 */
const vjsdownload = function(options) {
  this.ready(() => {
    onPlayerReady(this, videojs.mergeOptions(defaults, options));
  });
};

// Register the plugin with video.js.
videojs.plugin('vjsdownload', vjsdownload);

export default vjsdownload;
