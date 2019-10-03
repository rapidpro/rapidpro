import RapidElement from './RapidElement';
import { property } from 'lit-element';

/**
 * FormElement is a component that appends a hidden input (outside of
 * its own shodow) with its value to be included in forms.
 */
export default class FormElement extends RapidElement {
  private hiddenInputs: HTMLInputElement[] = [];

  @property({type: Array})
  values: any[] = [];

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

  private updateInputs(): void {
    for (const ele of this.hiddenInputs) {
      ele.remove();
    }

    for (const value of this.values) {
      const ele = document.createElement("input");
      ele.setAttribute("type", "hidden");
      ele.setAttribute("name", this.getAttribute("name"));
      ele.setAttribute("value", JSON.stringify(value));
      this.hiddenInputs.push(ele);
      this.inputRoot.appendChild(ele);
    }
  }

  /* public firstUpdated(changedProperties: any) {    
    // create our hidden container so it gets included in our host element's form
    this.updateInputs();
  }*/


  public updated(changedProperties: Map<string, any>) {
    super.updated(changedProperties);

    // if our cursor changed, lets make sure our scrollbox is showing it
    if(changedProperties.has("values")) {
      this.updateInputs();
    }
  }
 
}
