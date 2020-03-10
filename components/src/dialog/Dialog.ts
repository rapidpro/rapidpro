import { customElement, property } from 'lit-element/lib/decorators';
import { TemplateResult, html, css } from 'lit-element';
import Button from '../button/Button';
import RapidElement from '../RapidElement';
import { CustomEventType } from '../interfaces';
import { styleMap } from 'lit-html/directives/style-map.js';
import { getClasses } from '../utils';

@customElement("rp-dialog")
export default class Dialog extends RapidElement {

  static get widths(): { [size: string]: string } {
    return {
      'small' : '400px',
      'medium' : '600px',
      'large' : '655px'
    }
  }

  static get styles() {
    return css`

      :host {
        position: absolute;
        z-index: 10000;
        font-family: var(--font-family);
      }

      .dialog-mask {
        width: 100%;
        background: rgba(0, 0, 0, .5);
        opacity: 0;
        visibility: hidden;
        position: fixed;
        top:0px;
        left:0px;
        transition: all ease-in 250ms;
      }

      .dialog-container {
        margin: 0px auto; 
        top: -300px;
        position: relative;
        transition: top ease-in-out 200ms;
        border-radius: var(--curvature); 
        box-shadow: 0px 0px 2px 4px rgba(0,0,0,.06);
        overflow: hidden;
        opacity: 0;
      }

      .dialog-body {
        background: #fff;
      }

      .dialog-mask.dialog-open {
        opacity: 1;
        visibility: visible;
      }

      .dialog-mask.dialog-open > .dialog-container {
        top: 20px;
        opacity: 1;
      }

      .dialog-mask.dialog-loading > .dialog-container {
        top: -300px;
      }

      .header-text {
        font-size: 20px;
        padding: 16px;
        font-weight: 200;
        color: var(--color-text-light);
        background: var(--color-primary-dark);
      }

      .dialog-footer {
        background: var(--color-primary-light);
        padding: 10px;
        display: flex;
        flex-flow: row-reverse;
      }

      rp-button {
        margin-left: 10px;
      }

      .dialog-body rp-loading {
        position: absolute;
        right: 12px;
        margin-top: -30px;
        padding-bottom: 9px;
        display: none;
      }

      #page-loader {
        text-align: center;
        padding-top: 30px;
        display: block;
        position: relative;
        opacity: 0;
        transition: opacity 1000ms ease-in 500ms;
        visibility: hidden;
      }

      .dialog-mask.dialog-loading #page-loader  {
        opacity: 1;
        visibility: visible;
      }

  `;
  }


  @property({type : Boolean})
  open: boolean;

  @property()
  header: string;

  @property()
  body: string;

  @property({type: Boolean})
  submitting: boolean;

  @property({type: Boolean})
  loading: boolean;

  @property()
  size: string = "medium";

  @property({type: String})
  primaryButtonName: string = "Ok";

  @property({type: String})
  cancelButtonName: string = "Cancel";

  @property()
  submittingName: string = "Saving";

  @property({attribute: false})
  onButtonClicked: (button: Button) => void;

  public constructor() {
    super();
  }

  public updated(changedProperties: Map<string, any>) {
    if (changedProperties.has("open")) {
      // make sure our buttons aren't in progress on show
      if (this.open) {
        this.shadowRoot.querySelectorAll("rp-button").forEach((button: Button)=>button.disabled = false);
        const inputs = this.querySelectorAll("textarea,input");
        if (inputs.length > 0) {
          window.setTimeout(()=>{
            (inputs[0] as any).focus();            
          }, 100);
        }
      }
    }
  }

  public handleClick(evt: MouseEvent) {
    const button = evt.currentTarget as Button;
    if (!button.disabled) {
      this.fireCustomEvent(CustomEventType.ButtonClicked, {button});
    }
  }

  private getDocumentHeight(): number {
    const body = document.body;
    const html = document.documentElement;
    return Math.max(body.scrollHeight, body.offsetHeight, html.clientHeight, html.scrollHeight, html.offsetHeight);
  }

  private handleKeyUp(event: KeyboardEvent) {
    if (event.key === "Escape") {
      // find our cancel button and click it
      this.shadowRoot.querySelectorAll("rp-button").forEach(
        (button: Button)=>{ if (button.name === this.cancelButtonName) {button.click()}}
      )
    }
  }

  private handleClickMask(event: MouseEvent) {
    if ((event.target as HTMLElement).id === "dialog-mask") {
      this.fireCustomEvent(CustomEventType.DialogHidden);
    }
  }

  public render(): TemplateResult {

    const height = this.getDocumentHeight();

    const maskStyle = { height: `${height + 100}px`}
    const dialogStyle = { width: Dialog.widths[this.size] }

    let header = this.header ? html`
      <div class="dialog-header">
        <div class="header-text">${this.header}</div>
      </div>` : null;

    return html`
        <div id="dialog-mask"  @click=${this.handleClickMask} class="dialog-mask ${getClasses({ 
          "dialog-open": this.open,
          "dialog-loading": this.loading
          })}" style=${styleMap(maskStyle)}>

          <rp-loading id="page-loader" units=6 size=12 color="#ccc"></rp-loading>

          <div @keyup=${this.handleKeyUp} style=${styleMap(dialogStyle)} class="dialog-container">
            ${header}
            <div class="dialog-body" @keypress=${this.handleKeyUp}>${this.body ? this.body : html`<slot></slot>`}
              <rp-loading units=6 size=8></rp-loading>
            </div>

            <div class="dialog-footer">
              ${this.primaryButtonName ? html`<rp-button @click=${this.handleClick} .name=${this.primaryButtonName} primary ?disabled=${this.submitting}>}</rp-button>`: null}
              <rp-button @click=${this.handleClick} name=${this.cancelButtonName} secondary></rp-button>
            </div>
          </div>      
        </div>

    `;
  }
}