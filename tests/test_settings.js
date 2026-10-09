// Execute the settings logic against a small DOM fixture; no browser or model calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const data = JSON.parse(execFileSync(process.env.TERMPILOT_TEST_PYTHON || 'python', ['-B', '-c',
  'import json; from termpilot import config as c; print(json.dumps(dict(config=c.DEFAULTS["supervisor"], fields=c.SUPERVISOR_FIELDS, providers=c.PROVIDERS, fieldProviders=c.FIELD_PROVIDERS)))'], {cwd: root, encoding: 'utf8'}));
const source = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');
const logic = source.slice(source.indexOf('  const settings ='), source.indexOf('  async function openSettings'));
assert.ok(logic.includes('function settingsCommand'));
const elements = {};
for (const [key, , kind] of data.fields) {
  const label = {hidden: false, classList: {toggle(name, hidden) {label.hidden = hidden;}}};
  elements['sf_' + key] = {value: data.config[key], checked: data.config[key], closest() {return label;}};
  if (kind === 'choice+text') elements['sf_' + key + '_txt'] = {value: ''};
}
const output = {textContent: ''};
const context = vm.createContext({document: {getElementById: id => elements[id]}, $: () => output});
vm.runInContext(logic + '\nglobalThis.fixture = {settings, settingsValues, settingsCommand};', context);
Object.assign(context.fixture.settings, data);
elements.sf_claude_model.value = '__custom__';
elements.sf_claude_model_txt.value = 'claude-custom-model';
for (const provider of ['codex', 'claude', 'antigravity', 'codex']) {
  elements.sf_provider.value = provider;
  const values = context.fixture.settingsValues();
  context.fixture.settingsCommand(values);
  const info = data.providers[provider];
  assert.equal(values.claude_model, 'claude-custom-model');
  assert.ok(output.textContent.startsWith(info.executable + ' '));
  assert.ok(output.textContent.includes(values[info.model_key]));
  assert.equal(output.textContent.includes('/loop'), provider === 'claude');
  for (const [field, owner] of Object.entries(data.fieldProviders)) {
    assert.equal(elements['sf_' + field].closest().hidden, owner !== provider, field);
  }
}
console.log('Settings UI logic: PASS (three providers, isolated fields, retained custom models).');
