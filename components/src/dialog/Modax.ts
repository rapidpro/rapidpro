import { customElement, property } from 'lit-element/lib/decorators';
import { TemplateResult, html, css } from 'lit-element';
import RapidElement from '../RapidElement';
import { getUrl, serialize, postUrl } from '../utils';
import axios, { AxiosResponse, CancelTokenSource } from 'axios';
import { unsafeHTML } from 'lit-html/directives/unsafe-html';
import TextInput from '../textinput/TextInput';
import { throws } from 'assert';

@customElement("rp-modax")
export default class Modax extends RapidElement {
  
  static get styles() {
    return css`
    
      fieldset {
        border: none;
        margin: 0;
        padding: 0;
      }

      .control-group {
        margin-bottom: 15px;
        display: block;
      }

      .form-actions {
        display: none;
      }

      .modax-body {
        padding: 20px;
      }

      rp-loading {
        margin: 0 auto;
        display: block;
        width: 150px;
      }

      ul.errorlist {
        margin-top: 8px;
        list-style-type: none;
        padding-left: 0;

      }

      ul.errorlist li {
          color: var(--color-error) !important;
          padding: 3px 8px;
          border-left: 6px solid var(--color-error);
      }
    `;
  }

  @property({type: String})
  header: string = "";

  @property({type: String})
  endpoint: string;

  @property({type: Boolean, reflect: true})
  open: boolean;

  @property({type: Boolean})
  fetching: boolean;

  @property({type: Boolean})
  submitting: boolean;

  @property({type: String})
  primaryName: string;

  @property({type: String})
  body: any = this.getLoading();


  private cancelToken: CancelTokenSource;

  private handleSlotClicked(): void {
    this.open = true;
  }

  private focusFirstInput(): void {
    window.setTimeout(()=>{ 
      const input = this.shadowRoot.querySelector("rp-textinput") as TextInput;
      if (input) {
        input.inputElement.click()
      }
    });
  }

  public updated(changes: Map<string, any>) {
    super.updated(changes);

    if(changes.has("open")) {
      if (this.open) {
        this.fetchForm();
      } else {

        // hide our body after our hiding animation is done
        window.setTimeout(()=>{
          this.body = this.getLoading();
          this.submitting = false;
        }, 500);
      }
    }

    if (changes.has("body")) {
      this.focusFirstInput();      
    }
  }

  private getLoading() {
    return html`<rp-loading units=6 size=8></rp-loading>`;
  }

  private updatePrimaryButton(): void {
    window.setTimeout(()=>{
      const primaryName = (this.shadowRoot.querySelector("input[type='submit']") as any).value;
      if (primaryName) {
        this.primaryName = primaryName;
      }
      this.submitting = false;
    },0);
  }

  private setBody(body: string) {

    // remove any existing on our previous body
    const scriptBlock = this.shadowRoot.querySelector(".scripts");
    for (const child of scriptBlock.children) {
      child.remove();
    }
    
    // parse out any scripts in the body
    const div = this.ownerDocument.createElement('div');
    div.innerHTML = body;
    const scripts = div.getElementsByTagName('script');
    
    // IE bleeds through, avoid bootstrap form spans that breaks layout
    const spans = div.getElementsByClassName('span12');
    for (const span of spans) {
      span.className = "";
    }

    const toAdd: any = [];
    // now add them in
    for (let i=scripts.length - 1; i >= 0; i--) {
      const script = this.ownerDocument.createElement("script");
      var code = scripts[i].innerText;
      script.appendChild(this.ownerDocument.createTextNode(code));
      toAdd.push(script);

      // remove it from our current body text
      div.removeChild(scripts[i]);
    }
    
    this.body = unsafeHTML(div.innerHTML);
    window.setTimeout(()=>{
      for (const script of toAdd) {
        scriptBlock.appendChild(script);
      }
    }, 0);
  }

  private fetchForm() {
    const CancelToken = axios.CancelToken;
    this.cancelToken = CancelToken.source();
    this.fetching = true;
    this.body = this.getLoading();
    getUrl(this.endpoint, this.cancelToken.token, true).then((response: AxiosResponse) => {
        this.setBody(response.data);
        this.updatePrimaryButton();
        this.fetching = false;
    });  
  }

  private handleDialogClick(evt: CustomEvent) {
    const button = evt.detail.button;
    if (!button.disabled) {
      if (button.name === this.primaryName) {
        this.submitting = true;
        const form = this.shadowRoot.querySelector('form');
        const postData = serialize(form);

        postUrl(this.endpoint, postData, true).then((response: AxiosResponse) => {
          window.setTimeout(()=>{
            const redirect = response.headers['temba-success'];
            if (redirect) {
              if (redirect === "hide") {
                this.open = false;
              } else {
                this.ownerDocument.location = redirect;
              }
            } else {
              this.setBody(response.data);
              this.updatePrimaryButton();
            }
          }, 2000);

        });
      }
    }
    
    if(button.name === "Cancel") {
      this.open = false;
      this.fetching = false;
      this.cancelToken.cancel();
    }
  }

  private handleDialogHidden() {
    this.cancelToken.cancel();
    this.open = false;
    this.fetching = false;
  }

  public render(): TemplateResult {
    return html`
      <rp-dialog 
        header=${this.header} 
        .open=${this.open}
        .loading=${this.fetching}
        .primaryButtonName=${this.primaryName}
        .submitting=${this.submitting}
        @rp-button-clicked=${this.handleDialogClick.bind(this)}
        @rp-dialog-hidden=${this.handleDialogHidden.bind(this)}
      >
        <div class="modax-body">
          ${this.body}
        </div>
        <div class="scripts"></div>
      </rp-dialog>
      <div class="slot-wrapper" @click=${this.handleSlotClicked}>
        <slot></slot>
      </div>
    `;
  }
}