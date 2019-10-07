import { AxiosResponse } from 'axios';
import { css, customElement, html, LitElement, property, TemplateResult } from 'lit-element';
import { FeatureProperties } from '../interfaces';
import { getUrl, postUrl } from '../utils';
import autosize from 'autosize';
import Button from '../button/Button';
import TextInput from '../textinput/TextInput';
import { styleMap } from 'lit-html/directives/style-map.js';
import FormElement from '../FormElement';

@customElement("alias-editor")
export default class AliasEditor extends LitElement {

  static get styles() {
    return css`

      :host {
        line-height: normal;
      }

      rp-textinput {
        height: 150px;
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
        display: inline;
        margin-left: 5px;
      }

      rp-label {
        margin-right: 3px;
        margin-bottom: 3px;
        vertical-align: top;
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
        color: var(--color-secondary-light);
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
        margin-right: 0px;
      }

   `;
  }

  @property({type: Array, attribute: false})
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
    const selection = evt.detail.selected as FeatureProperties;
    this.showAliasDialog(selection);
    const select = this.shadowRoot.querySelector("rp-select") as FormElement;
    select.clear();
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

        <div class="aliases">
          ${feature.aliases.split('\n').map((alias: string)=>alias.trim().length > 0 ? html`
            <rp-label class="alias" @click=${()=>{this.showAliasDialog(feature);}} light clickable>${alias}</rp-label>
          `: null)}

          ${feature.level > 0 ? html`
          <div class="edit clickable showonhover" @click=${(evt: MouseEvent)=> { this.showAliasDialog(feature); evt.preventDefault(); evt.stopPropagation()}}>
            <rp-icon name="register" size="12"></rp-icon>
          </div>`: ''}
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

    this.requestUpdate();
  }

  private getEndpoint(): string {
    return this.endpoint + (!this.endpoint.endsWith('/') ? '/' : '');
  }

  private handleDialogClick(evt: CustomEvent) {
    const button = evt.detail.button;
    if (button.name === "Save") {
      button.setProgress(true);
      const textarea = this.shadowRoot.getElementById(this.editFeature.osm_id) as TextInput;
      const aliases = textarea.inputElement.value;
      const payload = { "osm_id":  this.editFeature.osm_id, aliases };
      postUrl(this.getEndpoint() + "boundaries/" +  this.editFeature.osm_id + "/", payload).then((response: AxiosResponse) => {
        this.fetchFeature();
      });
    }

    if(button.name === "Cancel") {
      this.hideAliasDialog();
    }
  }

  private getOptions(response: AxiosResponse) {
    return response.data.filter((option: any) => option.level > 0);
  }

  private getOptionsComplete(newestOptions: FeatureProperties[], response: AxiosResponse) {
    return newestOptions.length === 0;
  }

  private renderOptionDetail(option: FeatureProperties, selected: boolean): TemplateResult {
    const labelStyles = {
      marginTop: '3px',
      marginRight: '3px'
    }

    const aliasList = option.aliases.split('\n');
    const aliases = aliasList.map((alias: string)=>alias.trim().length > 0 ? html`<rp-label style=${styleMap(labelStyles)} class="alias" dark>${alias}</rp-label>`: null);
    return html`<div class="path">${option.path.replace(/>/gi, "‣")}</div><div class="aliases">${aliases}</div>`;    
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
          <rp-select
            placeholder="Search" 
            endpoint="${this.getEndpoint()}boundaries/${this.path[0].osm_id}/?"
            .renderOptionDetail=${this.renderOptionDetail}
            .getOptions=${this.getOptions}
            .isComplete=${this.getOptionsComplete}
            @rp-selection=${this.handleSearchSelection.bind(this)}
            searchable
          ></rp-select>
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
        @rp-button-clicked=${this.handleDialogClick.bind(this)}>

        <div class="selected">
          <rp-textinput name="aliases" id=${editFeatureId} .value=${editFeatureAliases} textarea></rp-textinput>
          <div class="help">
            Enter other aliases for ${editFeatureName}, one per line
          </div>
        </div>
      </rp-dialog>             

    `;
  }
}