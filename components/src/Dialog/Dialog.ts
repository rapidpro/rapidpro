import { customElement, property } from 'lit-element/lib/decorators';
import { LitElement, TemplateResult, html, css } from 'lit-element';
import "./../Button/Button";
import Button from './../Button/Button';

@customElement("rp-dialog")
export default class Dialog extends LitElement {

  static get styles() {
    return css`
      :host {
        font-family: 'Helvetica Neue', 'RobotoThin', sans-serif;
        font-size: 13px;
        font-weight: 200;
      }

      .mask {
        width: 100%;
        background: rgba(0, 0, 0, .5);
        opacity: 0;
        visibility: hidden;
        position: fixed;
        top:0px;
        left:0px;
        z-index: 2000;
        transition: all ease-in 250ms;
      }

      .dialog {
        background: #fff;
        width: 500px;
        margin: 0px auto; 
        top: -200px;
        position: relative;
        transition: top ease-in-out 200ms;
        border-radius: var(--curvature);
        overflow: hidden;
        box-shadow: 0px 0px 0px 4px rgba(0,0,0,.04);
      }

      .mask.open {
        opacity: 1;
        visibility: visible;
      }

      .title {
        padding: 10px 20px;
        font-size: 18px;
        color: #fff;
        background: var(--color-primary);
      }

      .footer {
        background: var(--color-bg-light);
        padding: 10px;
        display: flex;
        flex-flow: row-reverse;
      }

      rp-button {
        margin-left: 3px;
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
      this.onButtonClicked(button);
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

    return html`
        <style>
          .mask {
            height: ${height + 100}px;
          }
          .mask.open > .dialog {
            top: 100px;
          }
        </style>
        <div class="mask ${this.open ? 'open' : ''}">
          <div @keyup=${this.handleKeyUp} class="dialog">
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