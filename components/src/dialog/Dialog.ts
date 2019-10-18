import { customElement, property } from 'lit-element/lib/decorators';
import { LitElement, TemplateResult, html, css } from 'lit-element';
import Button from '../button/Button';
import RapidElement from '../RapidElement';
import { CustomEventType } from '../interfaces';
import { styleMap } from 'lit-html/directives/style-map.js';

@customElement("rp-dialog")
export default class Dialog extends RapidElement {

  static get widths(): { [size: string]: string } {
    return {
      'small' : '350px',
      'medium' : '500px',
      'large' : '655px'
    }
  }

  static get styles() {
    return css`

      :host {
        position: absolute;
        z-index: 10000;
      }

      .mask {
        width: 100%;
        background: rgba(0, 0, 0, .5);
        opacity: 0;
        visibility: hidden;
        position: fixed;
        top:0px;
        left:0px;
        transition: all ease-in 250ms;
      }

      .dialog {
        margin: 0px auto; 
        top: -200px;
        position: relative;
        transition: top ease-in-out 200ms;
        border-radius: var(--curvature); 
        box-shadow: 0px 0px 2px 4px rgba(0,0,0,.06);
        overflow: hidden;
      }

      .body {
        background: #fff;
      }

      .mask.open {
        opacity: 1;
        visibility: visible;
      }

      .mask.open > .dialog {
        top: 100px;
      }

      .title {
        font-size: 20px;
        padding: 16px;
        font-weight: 300;
        color: var(--color-text-light);
        background: var(--color-primary-dark);
      }

      .footer {
        background: var(--color-primary-light);
        padding: 10px;
        display: flex;
        flex-flow: row-reverse;
      }

      rp-button {
        margin-left: 10px;
      }
  `;
  }


  @property({type : Boolean})
  open: boolean;

  @property()
  title: string;

  @property()
  body: string;

  @property()
  size: string = "medium";

  @property()
  primaryButtonName: string = "Ok";

  @property({type: String})
  cancelButtonName: string = "Cancel";

  @property()
  inProgressName: string = "Saving";

  @property({attribute: false})
  onButtonClicked: (button: Button) => void;

  public constructor() {
    super();
  }

  public updated(changedProperties: Map<string, any>) {
    if (changedProperties.has("open")) {
      // make sure our buttons aren't in progress on show
      if (this.open) {
        this.shadowRoot.querySelectorAll("rp-button").forEach((button: Button)=>button.setProgress(false));
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
    if (!button.isProgress) {
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

  public render(): TemplateResult {

    const height = this.getDocumentHeight();

    const maskStyle = { height: `${height + 100}px`}
    const dialogStyle = { width: Dialog.widths[this.size] }

    return html`
        <div class="mask ${this.open ? 'open' : ''}" style=${styleMap(maskStyle)}>
          <div @keyup=${this.handleKeyUp} style=${styleMap(dialogStyle)} class="dialog">
            <div class="header">
              <div class="title">${this.title}</div>
            </div>
            <div class="body" @keypress=${this.handleKeyUp}>${this.body ? this.body : html`<slot></slot>`}</div>
            <div class="footer">
              <rp-button @click=${this.handleClick} name=${this.primaryButtonName} inProgessName=${this.inProgressName} primary>}</rp-button>
              <rp-button @click=${this.handleClick} name=${this.cancelButtonName} secondary></rp-button>
            </div>
          </div>      
        </div>

    `;
  }
}