export interface URN {
    scheme: string;
    path: string;
}

export interface Contact {
    urns: URN[];
    primary_urn_formatted: string;
    name: string;
    fields: {[uuid: string]: { text: string }};
    created_on: Date;
}

export interface FeatureProperties {
    name: string;
    osm_id: string;
    level: number;
    children?: FeatureProperties[];
    has_children?: boolean;
    aliases?: string;
    parent_osm_id?: string;
    id?: number;
    path?: string;
}

export enum CustomEventType {
    Canceled = 'rp-canceled',
    CursorChanged = 'rp-cursor-changed',
    Selection = 'rp-selection',
    ButtonClicked = 'rp-button-clicked'
}