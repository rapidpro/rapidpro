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
