/**
 * @file volume-menu-button.js
 */
import * as Fn from '../utils/fn.js';
import Component from '../component.js';
import Popup from '../popup/popup.js';
import PopupButton from '../popup/popup-button.js';
import MuteToggle from './mute-toggle.js';
import VolumeBar from './volume-control/volume-bar.js';
import document from 'global/document';

/**
 * Button for volume popup
 *
 * @param {Player|Object} player
 * @param {Object=} options
 * @extends PopupButton
 * @class VolumeMenuButton
 */
class VolumeMenuButton extends PopupButton {

  constructor(player, options={}){
    // Default to inline
    if (options.inline === undefined) {
      options.inline = true;
    }

    // If the vertical option isn't passed at all, default to true.
    if (options.vertical === undefined) {
      // If an inline volumeMenuButton is used, we should default to using
      // a horizontal slider for obvious reasons.
      if (options.inline) {
        options.vertical = false;
      } else {
        options.vertical = true;
      }
    }

    // The vertical option needs to be set on the volumeBar as well,
    // since that will need to be passed along to the VolumeBar constructor
    options.volumeBar = options.volumeBar || {};
    options.volumeBar.vertical = !!options.vertical;

    super(player, options);

    // Same listeners as MuteToggle
    this.on(player, 'volumechange', this.volumeUpdate);
    this.on(player, 'loadstart', this.volumeUpdate);

    // hide mute toggle if the current tech doesn't support volume control
    function updateVisibility() {
      if (player.tech_ && player.tech_['featuresVolumeControl'] === false) {
        this.addClass('vjs-hidden');
      } else {
        this.removeClass('vjs-hidden');
      }
    }

    updateVisibility.call(this);
    this.on(player, 'loadstart', updateVisibility);

    this.on(this.volumeBar, ['slideractive', 'focus'], function(){
      this.addClass('vjs-slider-active');
    });

    this.on(this.volumeBar, ['sliderinactive', 'blur'], function(){
      this.removeClass('vjs-slider-active');
    });

    this.on(this.volumeBar, ['focus'], function(){
      this.addClass('vjs-lock-showing');
    });

    this.on(this.volumeBar, ['blur'], function(){
      this.removeClass('vjs-lock-showing');
    });
  }

  /**
   * Allow sub components to stack CSS class names
   *
   * @return {String} The constructed class name
   * @method buildCSSClass
   */
  buildCSSClass() {
    let orientationClass = '';
    if (!!this.options_.vertical) {
      orientationClass = 'vjs-volume-menu-button-vertical';
    } else {
      orientationClass = 'vjs-volume-menu-button-horizontal';
    }

    return `vjs-volume-menu-button ${super.buildCSSClass()} ${orientationClass}`;
  }

  /**
   * Allow sub components to stack CSS class names
   *
   * @return {Popup} The volume popup button
   * @method createPopup
   */
  createPopup() {
    let popup = new Popup(this.player_, {
      contentElType: 'div'
    });

    let vb = new VolumeBar(this.player_, this.options_.volumeBar);

    popup.addChild(vb);

    this.volumeBar = vb;

    this.attachVolumeBarEvents();

    return popup;
  }

  /**
   * Handle click on volume popup and calls super
   *
   * @method handleClick
   */
  handleClick() {
    MuteToggle.prototype.handleClick.call(this);
    super.handleClick();
  }

  attachVolumeBarEvents() {
    this.on(['mousedown', 'touchdown'], this.handleMouseDown);
  }

  handleMouseDown(event) {
    this.on(['mousemove', 'touchmove'], Fn.bind(this.volumeBar, this.volumeBar.handleMouseMove));
    this.on(document, ['mouseup', 'touchend'], this.handleMouseUp);
  }

  handleMouseUp(event) {
    this.off(['mousemove', 'touchmove'], Fn.bind(this.volumeBar, this.volumeBar.handleMouseMove));
  }
}

VolumeMenuButton.prototype.volumeUpdate = MuteToggle.prototype.update;
VolumeMenuButton.prototype.controlText_ = 'Mute';

Component.registerComponent('VolumeMenuButton', VolumeMenuButton);
export default VolumeMenuButton;
