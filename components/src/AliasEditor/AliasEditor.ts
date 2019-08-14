import './../LeafletMap/LeafletMap';
import './../Dialog/Dialog';
import './../Choice/Choice';
import './../Label/Label';

import { AxiosResponse } from 'axios';
import { css, customElement, html, LitElement, property, TemplateResult } from 'lit-element';

import { FeatureProperties } from '../interfaces';
import { getUrl, postUrl } from '../utils';
import Button from '../Button/Button';
import autosize from 'autosize';


@customElement("alias-editor")
export default class AliasEditor extends LitElement {

  static get styles() {
    return css`

      :host {
        font-family: 'Helvetica Neue', 'RobotoThin', sans-serif;
        font-size: 13px;
        font-weight: 200;
      }

      textarea {
        border-radius: 5px;
        border-color: #ccc;
        padding: 10px;
        color: var(--color-textarea);
        font-size: 14px;
        resize: none;
      }

      textarea:focus {
        box-shadow: none;
        outline: none;
      }

      #left-column {
        display: inline-block;
        margin-left: 10px;
        width: 300px;
        z-index: 100;
      }

      .search {
        margin-bottom: 10px;
      }

      .feature {
        padding: 4px 14px;
        font-size: 16px;
      }

      .level-0 {
        margin-left: 0px;
      }

      .level-1 {
        margin-left: 5px;
        font-size: 95%;
      }

      .level-2 {
        margin-left: 10px;
        font-size: 90%;
      }

      .level-3 {
        margin-left: 15px;
        font-size: 85%;
      }

      .feature-name {
        display: inline-block;
      }

      .clickable {
        text-decoration: none;
        cursor: pointer;
        color: var(--color-link-primary);
      }

      .clickable.secondary {
        color: var(--color-link-secondary);
      }

      .clickable:hover {
        text-decoration: underline;
        color: var(--color-link-primary-hover);
      }

      .feature:hover .showonhover {
        visibility: visible;
      }

      .showonhover {
        visibility: hidden;
      }

      .aliases {
        color: #bbb;
        font-size: 80%;
        
      }

      rp-label {
        margin-top: 3px;
        margin-right: 3px;
        margin-bottom: 3px;
      }

      .selected {
        display: flex;
        flex-direction: column;
        padding: 15px;
      }

      .selected .name {
        font-size: 18px;
        padding: 5px;
      }

      .selected .help {
        padding: 5px 2px;
        font-size: 11px;
        color: var(--color-help);
      }

      #right-column {
        vertical-align: top;
        margin-left: 20px;
        display: inline-block;
      }

      leaflet-map {
        height: 250px;
        width: 450px;
        border: 0px solid #999;
        border-radius: 5px;
      }

      .edit {
        display: inline-block;
        font-size: 11px;
        margin-left: 3px;
      }

   `;
  }

  @property({type: Array})
  path: FeatureProperties[] = [];

  @property()
  endpoint: string;

  @property()
  osmId: string;

  @property({type: Object})
  hovered: FeatureProperties;

  @property({type: Object})
  editFeature: FeatureProperties

  public constructor() {
    super();
  }

  public updated(changedProperties: Map<string, any>) {

    if (changedProperties.has("osmId")) {
      // going up the tree doesn't require a fetch
      const newPath = [];
      for (let feature of this.path) {
        newPath.push(feature);
        if (feature.osm_id === this.osmId) {
          this.path = [...newPath];
          this.hideAliasDialog();
          return;
        }
      }

      this.fetchFeature();
    }
  }

  private fetchFeature() {
    getUrl(this.getEndpoint() + "boundaries/" + this.osmId + "/").then((response: AxiosResponse) => {
      this.path = response.data as FeatureProperties[];
      this.hideAliasDialog();
    });
  }

  /*
   Makes sure our textarea grows with us
   */
  private fireTextareaAutosize(): void {
    window.setTimeout(()=>{
      autosize(this.shadowRoot.querySelector('textarea'));
      autosize.update(this.shadowRoot.querySelector('textarea'));
    }, 0);
  }
  
  private handleMapClicked(feature: FeatureProperties): void {
    this.hovered = null;
    if (!feature || feature.osm_id !== this.osmId) {
      this.osmId = feature.osm_id;
    }
  }

  private handlePlaceClicked(feature: FeatureProperties) {
    this.osmId = feature.osm_id;
  }

  private handleSearchSelection(evt: CustomEvent) {
    const selection = evt.detail as FeatureProperties[];
    this.showAliasDialog(selection[0]);
  }

  private renderFeature(feature: FeatureProperties, remainingPath: FeatureProperties[]): TemplateResult {
    const selectedFeature = this.path[this.path.length - 1];
    const clickable = ((feature.has_children || feature.level === 0 )&& feature !== selectedFeature);
    const renderedFeature = html`
      <div class="feature">
        <div 
          @mouseover=${() => { if (feature.level > 0) { this.hovered = feature }}}
          @mouseout=${() => { this.hovered = null }}
          class="level-${feature.level}"
        >
        <div class="feature-name ${ clickable ? 'clickable' : ''}" 
          @click=${() => { if (clickable) {this.handlePlaceClicked(feature) }}}>
          ${feature.name}
        </div>
        ${feature.level > 0 && !feature.aliases ? html`<div class="edit clickable secondary showonhover" @click=${(evt: MouseEvent)=> { this.showAliasDialog(feature); evt.preventDefault(); evt.stopPropagation()}}>Add aliases</div>`: ''}        

        <div class="aliases">
          ${feature.aliases.split('\n').map((alias: string)=>alias.trim().length > 0 ? html`
            <rp-label class="alias" @click=${()=>{this.showAliasDialog(feature);}} secondary clickable>${alias}</rp-label>
          `: null)}
        </div>

      </div>
        
      </div>
      `;

    const renderedChildren = (feature.children || []).map((child: FeatureProperties)=> {
    
      if (remainingPath.length > 0 && (remainingPath[0].osm_id === child.osm_id)) {
        return this.renderFeature(remainingPath[0], remainingPath.slice(1));
      }

      if (remainingPath.length === 0 || remainingPath[0].children.length === 0) {
        return this.renderFeature(child, remainingPath);
      }

      return null;
    
    });

    return html`
      ${renderedFeature}
      ${renderedChildren}
    `
  }

  public showAliasDialog(feature: FeatureProperties) {
    this.editFeature = feature;
    const aliasDialog = this.shadowRoot.getElementById("alias-dialog");
    if (aliasDialog){
      this.fireTextareaAutosize();
      aliasDialog.setAttribute('open', '');
    }
  }

  public hideAliasDialog() {
    const aliasDialog = this.shadowRoot.getElementById("alias-dialog");
    if (aliasDialog) {
      aliasDialog.removeAttribute("open");
    }

    this.editFeature = null;
    this.requestUpdate();
  }

  private getEndpoint(): string {
    return this.endpoint + (!this.endpoint.endsWith('/') ? '/' : '');
  }

  private handleDialogClick(button: Button) {
    if (button.name === "Save") {
      button.setProgress(true);
      const textarea = this.shadowRoot.getElementById(this.editFeature.osm_id) as HTMLTextAreaElement;
      const aliases = textarea.value;
      const payload = { "osm_id":  this.editFeature.osm_id, aliases };
      postUrl(this.getEndpoint() + "boundaries/" +  this.editFeature.osm_id + "/", payload).then((response: AxiosResponse) => {
        this.fetchFeature();
      });
    }

    if(button.name === "Cancel") {
      this.editFeature = null;
      this.hideAliasDialog();
    }
  }

  private renderSearchOption(option: FeatureProperties, selected: boolean): TemplateResult {

    const style = html`
      <style>
        .name {
          font-size: 16px;
        }

        .path {
          font-size: 13px;
          color: rgba(140,140,140,.9);
        }

        rp-label {
          margin-top: 3px;
          margin-right: 3px;
        }

      </style>
    `
    const aliasList = option.aliases.split('\n');
    const aliases = aliasList.map((alias: string)=>alias.trim().length > 0 ? html`<rp-label class="alias" secondary>${alias}</rp-label>`: null);
    
    if (selected){
      const path = option.path.replace(/>/gi, "‣");
      return html`${style}<div class="name">${option.name}</div><div class="path">${path}</div><div class="aliases">${aliases}</div>`
    } else {
      return html`${style}<div class="name">${option.name}</div><div class="aliases">${aliases}</div>`
    }
  }

  public render(): TemplateResult {
    if (this.path.length === 0) {
      return html``;
    }

    // if we are a leaf, have our map show the level above
    const selectedFeature = this.path[this.path.length - 1];
    const mapFeature = selectedFeature.children.length === 0 ? this.path[this.path.length - 2] : selectedFeature;
    
    const editFeatureId = this.editFeature ? this.editFeature.osm_id : null;
    const editFeatureName = this.editFeature ? this.editFeature.name : null;
    const editFeatureAliases = this.editFeature ? this.editFeature.aliases : null;
    
    return html`
      <div id="left-column">
        <div class="search">
          <rp-choice 
            placeholder="Search" 
            endpoint="${this.getEndpoint()}boundaries/${this.path[0].osm_id}?q="
            @rp-choice-selected=${this.handleSearchSelection.bind(this)}
            .renderOption=${this.renderSearchOption}
          ></rp-choice>
        </div>

        <div class="feature-tree">
          ${this.renderFeature(this.path[0], this.path.slice(1))}
        </div>
      </div>

      <div id="right-column">
        <leaflet-map 
          endpoint=${this.getEndpoint()}
          .feature=${mapFeature}
          .osmId=${mapFeature.osm_id}
          .hovered=${this.hovered}
          .onFeatureClicked=${this.handleMapClicked.bind(this)}>
        </leaflet-map>
      </div>

      <rp-dialog id="alias-dialog" 
        title="Aliases for ${editFeatureName}" 
        primaryButtonName="Save" 
        .onButtonClicked=${this.handleDialogClick.bind(this)}>

        <div class="selected">
          <textarea id="${editFeatureId}" .value=${editFeatureAliases}></textarea>
          <div class="help">
            Enter other aliases for ${editFeatureName}, one per line
          </div>
        </div>
      </rp-dialog>             

    `;
  }
}