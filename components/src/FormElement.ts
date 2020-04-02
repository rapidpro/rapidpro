import RapidElement from './RapidElement';
import { property } from 'lit-element';

/**
 * FormElement is a component that appends a hidden input (outside of
 * its own shadow) with its value to be included in forms.
 */
export default class FormElement extends RapidElement {
  private hiddenInputs: HTMLInputElement[] = [];

  @property({type: String, attribute: "help_text"})
  helpText: string;

  @property({type: Boolean, attribute: "widget_only"})
  widgetOnly: boolean;

  @property({type: String})
  label: string;
  
  @property({type: Array})
  errors: string[];
  
  @property({type: Array})
  values: any[] = [];

  @property({type: String})
  value: string = '';

  @property({attribute: false})
  inputRoot: HTMLElement = this;

  public setValue(value: any) {
    this.setValues([value]);
  }

  public setValues(values: any[]) {
    this.values = values;
    this.requestUpdate("values");
  }

  public addValue(value: any) {
    this.values.push(value);
    this.requestUpdate("values");
  }

  public removeValue(valueToRemove: any) {
    this.values = this.values.filter((value: any) => value !== valueToRemove)
    this.requestUpdate("values");
  }

  public popValue() { 
    this.values.pop();
    this.requestUpdate("values");
  }

  public clear() { 
    this.values = [];
    this.requestUpdate("values");
  }

  public serializeValue(value: any): string {
    return JSON.stringify(value);
  }

  private updateInputs(): void {
    for(let ele = null; ele = this.hiddenInputs.pop();) {
      ele.remove();
    }

    for (const value of this.values) {
      const ele = document.createElement("input");
      ele.setAttribute("type", "hidden");
      ele.setAttribute("name", this.getAttribute("name"));
      ele.setAttribute("value", this.serializeValue(value));
      this.hiddenInputs.push(ele);
      this.inputRoot.parentElement.appendChild(ele);
    }
  }

  public updated(changedProperties: Map<string, any>) {
    super.updated(changedProperties);

    // if our cursor changed, lets make sure our scrollbox is showing it
    if(changedProperties.has("values")) {
      this.updateInputs();
    }
  }
 
}
