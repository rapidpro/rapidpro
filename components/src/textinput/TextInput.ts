import { customElement, TemplateResult, html, css, property } from 'lit-element';
import RapidElement from '../RapidElement';

@customElement("rp-textinput")
export default class TextInput extends RapidElement {
  static get styles() {
    return css`
      
      .input-container {
        border-radius: 5px;
        overflow: hidden;
        cursor: pointer;

        background: var(--color-widget-bg);
        border: 1px solid var(--color-widget-border);
        box-shadow: none;
        transition: all ease-in-out 200ms;
      }

      .input-container:focus-within {
        border-color: 1px solid var(--color-widget-border);
        box-shadow: var(--color-widget-shadow-focused);
        background: var(--color-widget-bg-focused);
      }

      .input-container:hover {
        background: var(--color-widget-bg-focused);
      }

      textarea {
        height: 85%;
      }

      .textinput {
        padding: 8px;
        border: none;
        width: 100%;
        margin: 0;
        background: transparent;
        color: var(--color-text);
        font-size: 13px;
        cursor: pointer;
        resize: none;
      }

      .textinput:focus {
        outline: none;
        cursor: text;
      }

    `
  }

  @property({type: Boolean})
  textarea: boolean;

  @property({type: String})
  value: string;

  @property({type: String})
  placeholder: string;

  private handleKeyUp(evt: KeyboardEvent) {
    this.value = (evt.target as HTMLInputElement).value.trim();
  }
  
  public render(): TemplateResult {
    return html`
    <style>
      .input-container {
        height: ${this.textarea ? '100%' : 'auto'};
      }
    </style>
    <div class="input-container" @click=${()=>{ (this.shadowRoot.querySelector(".textinput") as HTMLInputElement).focus()}}>\
      ${this.textarea ? html`
        <textarea class="textinput" .value=${this.value} @keyup=${this.handleKeyUp} placeholder=${this.placeholder}></textarea>
      ` : html`
        <input class="textinput" .value=${this.value} @keyup=${this.handleKeyUp} placeholder=${this.placeholder}>
      `}
    </div>
    `;
  }
}
