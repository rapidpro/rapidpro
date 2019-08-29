import { customElement, TemplateResult, html, css } from 'lit-element';
import RapidElement from '../RapidElement';

@customElement("rp-completion")
export default class Completion extends RapidElement {
  static get styles() {
    return css`
      :host {
        width: 100%;
        height: 100%;
      }

      textarea, input {
        border: 0;
        width: 100%;
        height: 100%;
        margin: 0;
        background: transparent;
        color: var(--color-text);
        font-size: 13px;
        cursor: pointer;
        resize: none;
      }

      textarea:focus, input:focus {
        outline: none;
        cursor: text;
      }

      .container {
        display: flex;
        flex-direction: column;
        border: 0px;
        height: 100%;
      }

      .input-container {
        padding: 8px 8px;
        border-radius: 5px;
        overflow: hidden;
        height: 100%;
        cursor: pointer;

        /* background: var(--color-widget-bg);*/        
        border: 1px solid var(--color-widget-border);
        background: rgba(0, 0, 0, 0.04);
        box-shadow: none;

        transition: all ease-in-out 200ms;
      }

      .input-container:focus-within, .input-container:hover {
        border-color: rgba(0, 0, 0, 0.07);
        box-shadow: rgba(0, 0, 0, 0.1) 0px 0px 2px 0px inset;
        background: rgba(0, 0, 0, 0.05);
      }
    `
  }
  
  public render(): TemplateResult {
    return html`<div class="container">
    <div class="input-container" @click=${()=>{ this.shadowRoot.querySelector("textarea").focus()}}>
      <textarea wrap="hard"></textarea>
    </div>
  </div>`;
  }
}
