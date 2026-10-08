"""Execute the SSO control behavior with Node and a small DOM fixture."""

import subprocess
from pathlib import Path


def test_sso_control_safe_labels_retry_and_back_navigation():
    module = (Path(__file__).resolve().parents[1] / 'static/js/loginSso.js').as_uri()
    script = r'''
import assert from 'node:assert/strict';
import { renderSso } from MODULE;
class Element {
  style = {}; attributes = {}; events = {}; textContent = ''; innerHTML = '';
  addEventListener(name, handler) { this.events[name] = handler; }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name]; }
  removeAttribute(name) { delete this.attributes[name]; }
  focus() { this.focused = true; }
}
const elements = Object.fromEntries(['oidcSection','oidcBtn','oidcLabel','signInStatus',
  'signInRetry','oidcIcon','oidcDivider'].map(id=>[id,new Element()]));
const doc = {getElementById: id => elements[id]};
const win = new Element();
renderSso(null, false, doc, win);
assert.equal(elements.oidcSection.style.display,'none');
assert.match(elements.signInStatus.textContent,/unavailable/);
assert.equal(elements.signInRetry.style.display,'');
renderSso({enabled:true,provider_name:'<img onerror=alert(1)>',provider_icon:'__proto__'},false,doc,win);
assert.equal(elements.oidcLabel.textContent,'Sign in with <img onerror=alert(1)>');
assert.equal(elements.oidcLabel.innerHTML,'');
assert.match(elements.oidcIcon.innerHTML, /^<path/);
assert.equal(elements.oidcDivider.style.display,'none');
assert.equal(elements.oidcBtn.focused,true);
let prevented = false;
const event = {preventDefault(){prevented=true;}};
elements.oidcBtn.events.click(event);
assert.equal(prevented,false);
assert.equal(elements.oidcBtn.getAttribute('aria-busy'),'true');
elements.oidcBtn.events.click(event);
assert.equal(prevented,true);
win.events.pageshow();
assert.equal(elements.oidcBtn.getAttribute('aria-disabled'),undefined);
assert.match(elements.oidcLabel.textContent,/^Sign in with /);
renderSso({enabled:true,provider_name:'SSO',provider_icon:'key'},true,doc,win);
assert.equal(elements.oidcDivider.style.display,'');
'''.replace('MODULE', repr(module))
    result = subprocess.run(['node', '--input-type=module', '-e', script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
