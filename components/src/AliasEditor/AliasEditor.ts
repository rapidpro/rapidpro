import './../LeafletMap/LeafletMap';

import { AxiosResponse } from 'axios';
import { css, customElement, html, LitElement, property, TemplateResult } from 'lit-element';

import { FeatureProperties } from '../interfaces';
import { getUrl } from '../utils';

@customElement("alias-editor")
export default class AliasEditor extends LitElement {

  static get styles() {
    return css`

      :host {
        font-family: 'Helvetica Neue', 'RobotoThin', sans-serif;
        font-size: 13px;
        font-weight: 200;
      }

      .names {
        display: inline-block;
        width: 250px;
        vertical-align: top;
      }

      .aliases {
        color: #bbb;
        font-size: 80%;
        display: inline-block;
        margin-left: 5px;
      }
      
      leaflet-map {
        height: 350px;
        width: 300px;
        border: 1px solid #999;
        border-radius: 5px;
        display: inline-block;
        vertical-align: top;
      }

      .place {
        color: #666;
        padding: 2px 6px;
      }

      .feature-name {
        text-decoration: none;
        cursor: pointer;
        display: inline-block;

      }

      .feature-name:hover {
        text-decoration: underline;
      }

    `;
  }

  @property()
  features: FeatureProperties[] = []

  @property()
  endpoint: string;

  @property()
  osmId: string;

  @property()
  hovered: FeatureProperties;

  _feature: FeatureProperties;

  public updated(changedProperties: Map<string, any>) {
    if (changedProperties.has("osmId")) {
      this.fetchFeatures();
    }
  }

  private fetchFeatures(): void {
    getUrl(this.endpoint + "boundaries/" + this.osmId + "/").then((response: AxiosResponse) => {
      this.features = response.data as FeatureProperties[];
    });
  }

  private handleMapClicked(feature: FeatureProperties): void {
    this.hovered = null;
    if (!feature || feature.osm_id !== this.osmId) {
      this.osmId = feature.osm_id;
      this._feature = feature;
    }
  }

  private handlePlaceClicked(feature: FeatureProperties) {
    this.osmId = feature.osm_id;
    this._feature = feature;
  }

  public firstUpdated(changedProperties: any): void {
    // console.log("first", changedProperties);
    // this.fetchFeatures();
  }

  public render(): TemplateResult {
    if (!this.osmId) {
      return html``;
    }

    const header = this._feature ? html`<div class="header">${this._feature.name}</div>` : null;

    return html`

      <leaflet-map 
        endpoint="${this.endpoint}"
        .feature="${this._feature}"
        .osmId="${this.osmId}"
        .hovered="${this.hovered}"
        .onFeatureClicked=${this.handleMapClicked.bind(this)}>
      </leaflet-map>

      <div class="names">
        ${this.features.map(
      (feature: FeatureProperties) =>
        html`<div class="place"  @mouseover=${() => { this.hovered = feature }} @mouseout=${() => { this.hovered = null }}>
              <div class="feature-name" @click=${() => { this.handlePlaceClicked(feature) }}>
                ${feature.name}
              </div>
              <div class="aliases">
                ${feature.aliases.split('\n').join(', ')}
              </div>
            </div>`)
      }
      </div>


    `;
  }
}