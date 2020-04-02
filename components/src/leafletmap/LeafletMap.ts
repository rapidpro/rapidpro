import { AxiosResponse } from 'axios';
import { Feature, Geometry } from 'geojson';
import { GeoJSON, geoJSON, LeafletEvent, LeafletMouseEvent, Map as RenderedMap, map, Path } from 'leaflet';
import { css, customElement, html, LitElement, property } from 'lit-element';

import { FeatureProperties } from '../interfaces';
import { getUrl } from '../utils';
import { highlightedFeature, normalFeature, visibleStyle } from './helpers';

@customElement("leaflet-map")
export default class LeafletMap extends LitElement {

  static get styles() {
    return css`
      :host {
        display: block;
        padding: 0px;        
      }

      #alias-map {
        top: 0px;
        height: 100%;
      }

      .leaflet-container {
        background: transparent;
      }

      .path {
        position: absolute;
        color: #666;
      }

      .path > .step {
        display: inline-block;
        font-size: 12px;
        margin-left: 5px;
      }

      .path > .step.hovered {
        color: #999;
      }

      .path > .step.linked {
        text-decoration: underline; 
        color: var(--color-link-primary);
        cursor: pointer;
      }
    `;
  }

  @property()
  feature: FeatureProperties;

  @property()
  osmId: string = "";

  @property()
  endpoint = "";

  @property()
  onFeatureClicked: (feature: FeatureProperties) => void;

  @property()
  hovered: FeatureProperties = null;

  @property()
  path: FeatureProperties[] = [];

  renderedMap: RenderedMap = null;
  states: GeoJSON<any> = null;

  constructor() {
    super();
  }

  private getRenderRoot(): DocumentFragment {
    return this.renderRoot as DocumentFragment;
  }

  private getEndpoint(): string {
    return this.endpoint + (!this.endpoint.endsWith('/') ? '/' : '');
  }

  paths: { [osmId: string]: Path } = {}
  lastHovered: Path = null;

  private refreshMap(): void {
    const onEachFeature = (feature: Feature<Geometry, any>, path: Path) => {

      this.paths[feature.properties.osm_id] = path;

      path.on({
        click: (event: LeafletMouseEvent) => {
          const feature: FeatureProperties = event.target.feature.properties
          if (feature.osm_id !== this.path[this.path.length - 1].osm_id) {
            const orig = event.originalEvent;

            orig.stopPropagation();
            orig.preventDefault();

            if (this.onFeatureClicked) {
              this.onFeatureClicked(feature);
            }

            this.hovered = null;
            this.path.push(feature);
            this.osmId = feature.osm_id;
            this.refreshMap();
          }
        },
        mouseover: (event: LeafletEvent) => {
          const feature: FeatureProperties = event.target.feature.properties
          if (feature.osm_id !== this.path[this.path.length - 1].osm_id) {
            event.target.setStyle(highlightedFeature);
            this.hovered = feature;
          }
        },
        mouseout: (event: LeafletEvent) => {
          event.target.setStyle(normalFeature);
          this.hovered = null;
        }
      });
    }



    getUrl(this.getEndpoint() + "geometry/" + this.osmId + "/").then((response: AxiosResponse) => {

      if (this.states) {
        this.renderedMap.removeLayer(this.states);
      }

      const data = response.data;
      if (this.path.length === 0) {
        this.path = [{
          name: data.name,
          osm_id: this.osmId,
          level: 0
        }];
      }

      this.states = geoJSON(data.geometry, { style: visibleStyle, onEachFeature });
      this.renderedMap.fitBounds(this.states.getBounds(), {});
      this.states.addTo(this.renderedMap);
    });
  }

  public updated(changedProperties: Map<string, any>) {

    if (changedProperties.has("hovered")) {
      if (this.lastHovered) {
        this.lastHovered.setStyle(normalFeature);
      }

      if (this.hovered) {
        const layer = this.paths[this.hovered.osm_id];
        this.lastHovered = layer;
        if (layer) {
          layer.setStyle(highlightedFeature);
        }
      }
    }

    if (changedProperties.has("feature") && this.feature) {
      this.hovered = null;
      if (this.path.length === 0 || this.path[this.path.length - 1].osm_id !== this.feature.osm_id) {
        this.path.push(this.feature);
      }
    }

    if (changedProperties.has("osmId")) {

      const path: FeatureProperties[] = [];
      for (const feature of this.path) {
        path.push(feature);
        if (feature.osm_id === this.osmId) {
          if (this.onFeatureClicked) {
            this.onFeatureClicked(feature);
          }
          break;
        }
      }

      this.path = path;

      this.refreshMap();
    }
  }


  public firstUpdated(changedProperties: any) {
    const mapElement = this.getRenderRoot().getElementById("alias-map");
    this.renderedMap = map(mapElement, { attributionControl: false, scrollWheelZoom: false, zoomControl: false }).setView([0, 1], 4);
    this.renderedMap.dragging.disable();
    this.renderedMap.doubleClickZoom.disable();

    this.refreshMap();
    super.firstUpdated(changedProperties);
  }

  private handleClickedBreadcrumb(e: MouseEvent): void {
    this.osmId = (e.currentTarget as HTMLElement).getAttribute("data-osmid");
    const path: FeatureProperties[] = [];
    for (const feature of this.path) {
      path.push(feature);
      if (feature.osm_id === this.osmId) {
        if (this.onFeatureClicked) {
          this.onFeatureClicked(feature);
        }
        break;
      }
    }

    this.path = path;
    this.refreshMap();
  }

  render() {
    if (!this.osmId) {
      return html`<div>No osm map id</div>`;
    }

    return html`
      <link rel="stylesheet" href="https://unpkg.com/leaflet@1.5.1/dist/leaflet.css"/>
      <div id="alias-map"></div>
    `;
  }
}
