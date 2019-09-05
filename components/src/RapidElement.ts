import { LitElement } from 'lit-element';
import { CustomEventType } from './interfaces';

export interface EventHandler {
  event: string;
  method: EventListener;
}

export default class RapidElement extends LitElement {

  public getEventHandlers(): EventHandler[] {
    return [];
  }

  connectedCallback() {
    super.connectedCallback();
    for (const handler of this.getEventHandlers()) {
      document.addEventListener(handler.event, handler.method.bind(this));
    }
  }

  disconnectedCallback() {
    for (const handler of this.getEventHandlers()) {
      document.removeEventListener(handler.event, handler.method);
    }
    super.disconnectedCallback();
  }

  public fireEvent(type: CustomEventType, detail: any = {}): void {
    const event = new CustomEvent(type, {
        detail,
        bubbles: true,
        composed: true
    });
    this.dispatchEvent(event);
  };
}