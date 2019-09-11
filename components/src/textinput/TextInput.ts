import { customElement, TemplateResult, html, css, property } from 'lit-element';
import RapidElement from '../RapidElement';

@customElement("rp-textinput")
export default class TextInput extends RapidElement {
  static get styles() {
    return css`
      
      .input-container {
        border-radius: var(--curvature);
        overflow: hidden;
        cursor: text;
        background: var(--color-widget-bg);
        border: 1px solid var(--color-widget-border);
        box-shadow: none;
        transition: all ease-in-out 200ms;
        display: flex;
        flex-direction: row;
        align-items: stretch;
      }

      .input-container:focus-within {
        border-color: var(--color-widget-border);
        background: var(--color-widget-bg-focused);
        box-shadow: var(--color-widget-shadow-focused) 1px 1px 3px 0px inset;
      }

      .input-container:hover {
        background: var(--color-widget-bg-focused);
      }

      textarea {
        height: 85%;
      }

      .textinput {
        padding: 8px;
        border: 0px solid red;
        flex: 1;
        margin: 0;
        background: none;
        color: var(--color-text);
        font-size: 13px;
        cursor: text;
        resize: none;
      }

      .textinput:focus {
        outline: none;
        cursor: text;
      }

      .textinput::placeholder {
        color: rgba(0,0,0,.15);
      }

    `
  }

  @property({type: Boolean})
  textarea: boolean;

  @property({type: String})
  placeholder: string = "";

  @property({type: String})
  value: string = "";

  @property({type: String})
  name: string = "";

  @property({type: Object})
  inputElement: HTMLInputElement;

  public firstUpdated(changes: Map<string, any>) {
    super.firstUpdated(changes);
    this.inputElement = this.shadowRoot.querySelector(".textinput");
  }

  public render(): TemplateResult {
    return html`
    <style>
      .input-container {
        height: ${this.textarea ? '100%' : 'auto'};
      }
    </style>
    <div class="input-container" @click=${()=>{ (this.shadowRoot.querySelector(".textinput") as HTMLInputElement).focus()}}>
      ${this.textarea ? html`
        <textarea class="textinput" 
          name=${this.name}
          placeholder=${this.placeholder}
          .value=${this.value}>
        </textarea>
      ` : html`
        <input class="textinput" 
          name=${this.name}
          type="text"
          placeholder=${this.placeholder}
          .value=${this.value}>
      `}
    </div>
    `;
  }
}
