import { customElement, TemplateResult, html, css, property } from 'lit-element';
import RapidElement, { EventHandler } from '../RapidElement';
import { styleMap } from 'lit-html/directives/style-map.js';

enum OmniType {
  Group = "group",
  Contact = "contact",
  Urn = "urn"
}

interface OmniOption {
  id: string;
  name: string;
  type: OmniType;
  urn?: string;
  count?: number;
  contact?: string;
  scheme?: string;
}

const iconStyle: any = {
  '--icon-color': 'var(--color-secondary-light)',
  'padding': '0 8px 0 0',
  'vertical-align': 'middle'
}


@customElement("rp-omnibox")
export default class Omnibox extends RapidElement {

  static get styles() {
    return css`
      rp-icon {
        padding-right: 5px;
      }
    `
  }      


  @property()
  endpoint: string;


  private renderOption(option: OmniOption, selected: boolean): TemplateResult {
    const style = { ...iconStyle};

    if (selected) {
      style['--icon-color'] = '#fff';
    }
    
    return html`<div class="name">${this.getIcon(option, selected, 14, style)}${option.name}</div>`;
  }

  private renderSelection(option: OmniOption): TemplateResult {
    const style = { ...iconStyle};
    style['padding'] = '0 0 0 4px';
    return html`${this.getIcon(option, false, 12, style)}<div class="name">${option.name}</div>`;
  }

  private getIcon(option: OmniOption, selected: boolean, size: number = 14, styles: any): TemplateResult {

    if (option.type === OmniType.Group) {
      return html`<rp-icon size=${size} style=${styleMap(styles)} name="group-two-filled"></rp-icon>`
    }

    if (option.type === OmniType.Contact) {
      return html`<rp-icon size=${size - 2} style=${styleMap(styles)} name="contact-filled"></rp-icon>`
    }
  }

  public render(): TemplateResult {
    return html`
      <rp-select 
        placeholder="Select recipients" 
        endpoint=${this.endpoint}
        .renderOption=${this.renderOption.bind(this)}
        .renderSelectedItem=${this.renderSelection.bind(this)}
        searchOnFocus
        multi
      ></rp-select>
      `;
  }
}
  