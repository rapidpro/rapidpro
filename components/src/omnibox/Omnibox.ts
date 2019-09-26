import { customElement, TemplateResult, html, css, property } from 'lit-element';
import RapidElement, { EventHandler } from '../RapidElement';

@customElement("rp-omnibox")
export default class Omnibox extends RapidElement {

  @property()
  endpoint: string;

  public render(): TemplateResult {
    return html`
      <rp-select 
        placeholder="Select recipients" 
        endpoint=${this.endpoint}
        searchOnFocus
        multi
      ></rp-select>
      `;
  }
}
  