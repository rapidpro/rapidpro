import { customElement, TemplateResult, html, css, property } from 'lit-element';
import RapidElement from '../RapidElement';
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
  '--icon-color': 'var(--color-text-dark-secondary)',
  'padding': '0 8px 0 0',
  'vertical-align': 'middle',
  'display': 'inline-block'
}

const detailStyle = {
  'margin-left': '5px',
  'font-size': '11px',
  'color': 'var(--color-text-dark-secondary)',
  'display': 'inline-block'
}

@customElement("rp-omnibox")
export default class Omnibox extends RapidElement {

  static get styles() {
    return css`
      rp-icon {
        padding-right: 5px;
      }

      rp-select:focus {
        outline: none;
        box-shadow: none;
      }
    `
  }      


  @property()
  endpoint: string;

  @property()
  name: string;

  @property({type: Boolean})
  groups: boolean = false;

  @property({type: Boolean})
  contacts: boolean = false;

  @property({type: Boolean})
  urns: boolean = false;

  @property({type: Array})
  value: OmniOption[] = [];

  @property()
  placeholder: string = 'Select recipients';

  private getDetail(option: OmniOption, selected: boolean = false): TemplateResult {

    const style = { ...detailStyle};
    if (selected) {
      style['color'] = '#fff';
    }

    if (option.urn && option.type === OmniType.Contact) {
      if (option.urn !== option.name) {
        return html`<div style=${styleMap(style)}>${option.urn}</div>`;
      }
    }

    if (option.type === OmniType.Group) {
      return html`<div style=${styleMap(style)}>(${option.count})</div>`;
    }

    return null;

  }

  private renderOption(option: OmniOption, selected: boolean): TemplateResult {
    const style = { ...iconStyle};

    if (selected) {
      style['--icon-color'] = '#fff';
    }
    
    return html`<div>${this.getIcon(option, selected, 14, style)}${option.name}${this.getDetail(option, selected)}</div>`;
  }

  private renderSelection(option: OmniOption): TemplateResult {
    const style = { ...iconStyle};
    style['padding'] = '0 4px 0 0';
    return html`<div class="name" style=${styleMap({'color': 'var(--color-text-dark)'})}>${this.getIcon(option, false, 12, style)}${option.name}${this.getDetail(option)}</div>`;
  }

  private getIcon(option: OmniOption, selected: boolean, size: number = 14, styles: any): TemplateResult {

    if (option.type === OmniType.Group) {
      return html`<rp-icon size=${size} style=${styleMap(styles)} name="group-two-filled"></rp-icon>`
    }

    if (option.type === OmniType.Contact) {
      return html`<rp-icon size=${size - 2} style=${styleMap(styles)} name="contact-filled"></rp-icon>`
    }
  }

  private getEndpoint() {
    const endpoint = this.endpoint;
    let types="&types=";
    if (this.groups) {
      types += "g";
    }

    if(this.contacts) {
      types += "c";
    }

    if(this.urns) {
      types += "u";
    }

    return endpoint + types;
  }

  public render(): TemplateResult {
    return html`
      <rp-select 
        name=${this.name}
        endpoint=${this.getEndpoint()}
        placeholder=${this.placeholder}
        queryParam="search"
        .values=${this.value}
        .renderOption=${this.renderOption.bind(this)}
        .renderSelectedItem=${this.renderSelection.bind(this)}
        .inputRoot=${this}
        searchable
        searchOnFocus
        multi
      ></rp-select>
      `;
  }
}
  