import { customElement, TemplateResult, html, css, property } from 'lit-element';
import RapidElement from '../RapidElement';

@customElement("rp-completion")
export default class Completion extends RapidElement {
  static get styles() {
    return css``
  }

  @property({type: String})
  placeholder: string;
  
  public render(): TemplateResult {
    return html`<rp-textinput placeholder=${this.placeholder}></rp-textinput>`;
  }
}
