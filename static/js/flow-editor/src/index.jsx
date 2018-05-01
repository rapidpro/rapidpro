// React v16 polyfills (https://reactjs.org/docs/javascript-environment-requirements.html)
import 'core-js/es6/map';
import 'core-js/es6/set';
import 'raf/polyfill';
import * as React from 'react';
import { render } from 'react-dom';
import FlowEditor from '@nyaruka/flow-editor';

const ele = document.getElementById('flow-editor');
const base = ele.getAttribute("base");
const engine = ele.getAttribute("engine");

const config = {
    flow: ele.getAttribute("uuid"),
    languages: {},
    localStorage: true,
    endpoints: {
        flows: `${base}/flow`,
        groups: `${base}/group`,
        contacts: `${base}/contact`,
        fields: `${base}/field`,
        activity: '',
        simulateStart: engine,
        simulateResume: engine
    }
};

render(<FlowEditor config={config} />, ele);
