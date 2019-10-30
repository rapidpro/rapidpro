import { customElement, TemplateResult, html, property, css } from 'lit-element';
import RapidElement from '../RapidElement';
import axios, { CancelTokenSource, AxiosResponse } from 'axios';
import { getUrl, plural, fillTemplate } from '../utils';
import TextInput from '../textinput/TextInput';
import '../alert/Alert';
import { Contact } from '../interfaces';
import { styleMap } from 'lit-html/directives/style-map';

const QUEIT_MILLIS = 1000;

interface SummaryResponse {
  total: number;
  sample: Contact[];
  query: string;
  fields: {[uuid: string]: { label: string, type: string}};
  error?: string;
}

@customElement("rp-contact-search")
export default class ContactSearch extends RapidElement {

  static get styles() {
    return css`

      :host {
        color: var(--color-text);
      }

      .urn {
        width: 120px;
      }

      .name {
        width: 160px;
      }

      .created-on {
        text-align: right;
      }

      .field-header {
        font-size: 80%;
        color: var(--color-text-dark);
      }

      .field-header.created-on { 
        text-align: right;
      }

      .more {
        font-size: 90%;
        padding-top: 5px;
        padding-right: 3px;
        text-align: right;
        width: 100px;
        vertical-align: top;
      }

      table {
        width: 100%;
        padding-top: 10px;
      }

      .header td {
        border-bottom: 2px solid var(--color-borders);
        padding: 5px 3px;
      }

      .contact td {
        border-bottom: 1px solid var(--color-borders);
        padding: 5px 3px;
      }

      .table-footer td {
        padding: 10px 3px;
      }

      .query-replaced, .count-replaced {
        display: inline-block;
        background: var(--color-primary-light);
        color: var(--color-text-dark);
        padding: 3px 6px;
        border-radius: var(--curvature);
        font-size: 85%;
        margin: 0px 3px;
      }

      rp-loading {
        margin-top: 10px;
        margin-right: 10px;
        opacity: 0;
      }

      .error {
        margin-top: 10px;
      }
    `
  }
  
  private cancelToken: CancelTokenSource;

  @property({type: Boolean})
  fetching: boolean;

  @property({type: String})
  endpoint: string;
  
  @property({type: String})
  placeholder: string = '';

  @property({type: String})
  name: string = '';

  @property({type: String})
  query: string = '';

  @property({type: String, attribute: 'matches-text'})
  matchesText: string = '';

  @property({attribute: false})
  summary: SummaryResponse;

  private lastQuery: number;

  public updated(changedProperties: Map<string, any>) {
    super.updated(changedProperties);

    if (changedProperties.has("query")) {

      this.fetching = !!this.query;

      // clear our summary on any change
      this.summary = null;
      if (this.lastQuery) {
        window.clearTimeout(this.lastQuery);
      }

      if (this.query.trim().length > 0) {
        this.lastQuery = window.setTimeout(()=>{
          this.fetchSummary(this.query);
        }, QUEIT_MILLIS);
      }
    }
  }

  public fetchSummary(query: string): any {
    const CancelToken = axios.CancelToken;
      this.cancelToken = CancelToken.source();

      const url = this.endpoint + query;

      getUrl(url, this.cancelToken.token).then((response: AxiosResponse) => {
        if(response.status === 200) {
          this.summary = response.data as SummaryResponse;
          this.fetching = false;
        }
      });
  }

  private handleQueryChange(evt: KeyboardEvent) {
    const input = evt.target as TextInput;
    this.query = input.inputElement.value;

  }

  public render(): TemplateResult {

    let summary: TemplateResult;
    if (this.summary) {
      const fields = Object.keys(this.summary.fields || []).map((uuid: string) => {
          return { uuid, ...this.summary.fields[uuid] }
      });

      if (!this.summary.error) {
        const count = this.summary.total;
        const message = fillTemplate(this.matchesText, { 
          query: this.summary.query,
          count
        });
        
        summary = html`
          <table cellspacing="0" cellpadding="0">
          <tr class="header">
            <td colspan="2"></td>
            ${fields.map((field) => html `
              <td class="field-header">${field.label}</td>
            `)}
            <td></td>
            <td class="field-header created-on">Created On</td>
          </tr>

          ${this.summary.sample.map((contact: Contact) => html`
            <tr class="contact">
              <td class="urn">${contact.primary_urn_formatted}</td>
              <td class="name">${contact.name}</td>
              ${fields.map((field) => html `
                <td class="field">${(contact.fields[field.uuid] || { text: ''}).text}</td>
              `)}
              <td></td>
              <td class="created-on">${contact.created_on}</td>
            </tr>
          `)}

          <tr class="table-footer">
            <td class="query-details" colspan=${fields.length + 3}>
              ${message}
            </td>
            <td class="more">${this.summary.total > this.summary.sample.length ? html`${this.summary.total - this.summary.sample.length} more`: null}</td>
          </tr>
          </table>
        `;
      } else {
        summary = html`<div class="error"><rp-alert level="error">${this.summary.error}</rp-alert></div>`
      }
    }

    const loadingStyle = this.fetching ? { 'opacity': '1'} : {}
    
    return html`
      <rp-textinput ?error=${!!(this.summary && this.summary.error)} name=${this.name} .inputRoot=${this} @input=${this.handleQueryChange} placeholder=${this.placeholder} value=${this.query}>
        <rp-loading units="4" style=${styleMap(loadingStyle)}></rp-loading>
      </rp-textinput>
      ${this.summary ? html `
        <div class="summary">
          ${summary}
        </div>
      `: null }
    `;
  }
}