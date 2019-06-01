import axios, { AxiosResponse } from 'axios';
import { html, TemplateResult } from 'lit-html';

/** Get the value for a named cookie */
export const getCookie = (name: string): string => {
    for (const cookie of document.cookie.split(';')) {
        const idx = cookie.indexOf('=');
        let key = cookie.substr(0, idx);
        let value = cookie.substr(idx + 1);

        // no spaces allowed
        key = key.trim();
        value = value.trim();

        if (key === name) {
            return value;
        }
    }
    return null;
};

export const getUrl = (url: string): Promise<AxiosResponse> => {
    const csrf = getCookie('csrftoken');
    const headers = csrf ? { 'X-CSRFToken': csrf } : {};
    return axios.get(url, { headers });
};

/**
 */
export const renderIf = (predicate: boolean | any) => (
    then: () => TemplateResult,
    otherwise?: () => TemplateResult
) => {
    return (predicate ? then() : otherwise ? otherwise() : html``)
};
