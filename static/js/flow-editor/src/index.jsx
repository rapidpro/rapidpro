// React v16 polyfills (https://reactjs.org/docs/javascript-environment-requirements.html)
import 'core-js/es6/map';
import 'core-js/es6/set';
import 'raf/polyfill';
import * as React from 'react';
import { render } from 'react-dom';
import FlowEditor from '@nyaruka/flow-editor';

const config = {
    flow: 'a4f64f1b-85bc-477e-b706-de313a022979',
    languages: {
        eng: 'English',
        spa: 'Spanish',
        fre: 'French'
    },
    localStorage: true,
    endpoints: {
        flows: '/assets/flows.json',
        groups: '/assets/groups.json',
        contacts: '/assets/contacts.json',
        fields: '/assets/fields.json',
        activity: '',
        engine: '/flow'
    }
};

render(<FlowEditor config={config} />, document.getElementById('flow-editor'));
