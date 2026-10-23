// Standalone local Chrome fallback: synthetic fixture only, no additional packages.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { createServer } from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';

class Connection {
  constructor(socket) {
    this.socket = socket;
    this.pending = new Map();
    this.events = new Map();
    this.sequence = 0;
    socket.addEventListener('message', ({ data }) => {
      const response = JSON.parse(data);
      if (response.method) { this.events.get(response.method)?.(response.params); return; }
      const pending = this.pending.get(response.id);
      if (!pending) return;
      this.pending.delete(response.id);
      response.error ? pending.reject(new Error(response.error.message)) : pending.resolve(response.result);
    });
  }
  async call(method, params = {}) {
    const id = ++this.sequence;
    const result = new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
    this.socket.send(JSON.stringify({ id, method, params }));
    return Promise.race([result, delay(10000).then(() => { throw new Error(`Timeout: ${method}`); })]);
  }
  async read(expression) {
    const result = await this.call('Runtime.evaluate', { expression, returnByValue: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result.value;
  }
  async until(expression) {
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline) {
      if (await this.read(expression)) return;
      await delay(50);
    }
    throw new Error(`DOM condition failed: ${expression}`);
  }
  async target(specification) {
    const result = await this.read(`(() => {
      const specification = ${JSON.stringify(specification)};
      const scope = [...document.querySelectorAll('[role=dialog]')].at(-1) || document;
      const candidates = [...scope.querySelectorAll(specification.tag)].filter(element => {
        const name = element.getAttribute('aria-label') || [...(element.labels || [])].map(label => label.textContent).join('') || element.textContent;
        return name.trim() === specification.name && element.getBoundingClientRect().width > 0;
      });
      if (candidates.length !== 1) return {count: candidates.length};
      const element = candidates[0], bounds = element.getBoundingClientRect();
      return {count: 1, disabled: element.disabled, x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2};
    })()`);
    assert.equal(result.count, 1, `Unique visible control: ${specification.name}`);
    assert.equal(result.disabled, false, `Enabled control: ${specification.name}`);
    return result;
  }
  async click(specification) {
    let target = await this.target(specification);
    const height = await this.read('innerHeight');
    if (target.y < 0 || target.y > height) {
      await this.call('Input.dispatchMouseEvent', { type: 'mouseWheel', x: target.x, y: height / 2, deltaX: 0, deltaY: target.y - height / 2 });
      await delay(100);
      target = await this.target(specification);
    }
    const { x, y } = target;
    await this.call('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
    await this.call('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
  }
  async fill(name, text) {
    await this.click({ tag: 'input,textarea', name });
    await this.call('Input.insertText', { text });
  }
  async screenshot(name) {
    const { data } = await this.call('Page.captureScreenshot', { format: 'png' });
    await writeFile(resolve(`build/browser-${name}.png`), Buffer.from(data, 'base64'));
  }
}

async function connect(profile) {
  let port;
  for (let attempt = 0; attempt < 100; attempt++) {
    try { port = Number((await readFile(`${profile}/DevToolsActivePort`, 'utf8')).split('\n')[0]); break; }
    catch (error) { if (error.code !== 'ENOENT') throw error; await delay(50); }
  }
  assert.ok(port, 'Chrome debugging port available');
  const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page = pages.find(page => page.type === 'page');
  assert.ok(page, 'Isolated browser tab available');
  const socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve); socket.addEventListener('error', reject); });
  return new Connection(socket);
}

async function responsive(connection) {
  for (const width of [360, 768, 1280]) {
    await connection.call('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: false });
    await connection.until(`document.querySelector('h1')?.textContent === 'Mailboxes'`);
    const layout = await connection.read(`({width: innerWidth, document: document.documentElement.scrollWidth, table: getComputedStyle(document.querySelector('.desktop-table')).display, cards: getComputedStyle(document.querySelector('.mailbox-cards')).display})`);
    assert.ok(layout.document <= width, `No horizontal overflow at ${width}: ${JSON.stringify(layout)}`);
    assert.equal(width >= 960 ? layout.cards : layout.table, 'none');
    await connection.screenshot(`mailboxes-${width}`);
  }
}

async function mailboxFlows(connection, fixture) {
  await connection.click({ tag: 'button', name: 'New mailbox' });
  await connection.until(`document.querySelector('[role=dialog]') !== null`);
  await connection.fill('Username prefix', 'ui-fixture');
  await connection.fill('Display name', 'Browser fixture');
  await connection.fill('Notes / purpose', '<img src=x onerror=window.__mailFixtureInjected=true>');
  await connection.fill('Incoming mailbox password', '  synthetic mailbox password 42  ');
  await connection.click({ tag: 'button', name: 'Create mailbox' });
  await connection.until(`document.querySelector('[role=dialog]') === null && document.querySelectorAll('.desktop-table tbody tr').length === 9`);
  await connection.fill('Search mailboxes', 'ui-fixture');
  await connection.until(`document.querySelectorAll('.desktop-table tbody tr').length === 1`);
  assert.equal(await connection.read(`document.querySelector('.desktop-table tbody').textContent.includes('<img src=x')`), true);
  assert.equal(await connection.read(`Boolean(window.__mailFixtureInjected) || Boolean(document.querySelector('img[src=x]'))`), false);
  await connection.click({ tag: 'button', name: 'Configure' });
  for (const name of ['Apple Mail', 'Gmail', 'Thunderbird / Outlook', 'Nextcloud', 'Raw details']) {
    await connection.click({ tag: 'button', name });
    assert.equal(await connection.read(`document.querySelector('[role=tab][aria-selected=true]').textContent`), name);
  }
  assert.equal(await connection.read(`document.querySelector('[role=dialog]').textContent.includes('hashes')`), true);
  await connection.click({ tag: 'button', name: 'Close' });
  await connection.click({ tag: 'button', name: 'Reset password' });
  await connection.fill('Incoming mailbox password', '  replacement fixture password 42  ');
  await connection.click({ tag: 'button', name: 'Reset password' });
  await connection.until(`document.querySelector('#reauth-title') !== null`);
  await connection.fill('Master password', fixture.password);
  await connection.click({ tag: 'button', name: 'Verify password' });
  await connection.until(`[...document.querySelectorAll('button')].some(button => button.textContent === 'Recovery code')`);
  await connection.click({ tag: 'button', name: 'Recovery code' });
  await connection.fill('Recovery code', fixture.recovery_code);
  await connection.click({ tag: 'button', name: 'Verify second factor' });
  await connection.until(`document.querySelector('[role=dialog]') === null`);
  assert.equal(await connection.read('localStorage.length + sessionStorage.length'), 0);
}

async function relayFlows(connection) {
  await connection.fill('Brevo SMTP login', 'synthetic-browser-login');
  await connection.fill('Brevo SMTP key', 'synthetic-browser-key');
  await connection.click({ tag: 'button', name: 'Save relay' });
  await connection.until(`document.body.textContent.includes('Saved revision 1')`);
  assert.equal(await connection.read(`document.querySelector('input[autocomplete="new-password"]').value`), '');
  await connection.click({ tag: 'button', name: 'Test connection' });
  await connection.until(`document.body.textContent.includes('Connection only · no email sent')`);
  assert.equal(await connection.read(`document.body.textContent.includes('Delivery unverified')`), true);
  await connection.click({ tag: 'button', name: 'Refresh provider credits' });
  await connection.until(`document.body.textContent.includes('No API key configured')`);
  assert.equal(await connection.read('localStorage.length + sessionStorage.length'), 0);
}

async function openSettings(connection) {
  await connection.call('Fetch.enable', { patterns: [{ urlPattern: '*/api/settings/relay', requestStage: 'Request' }] });
  const request = new Promise(resolve => connection.events.set('Fetch.requestPaused', resolve));
  await connection.click({ tag: 'button', name: 'Settings' });
  await connection.until(`document.querySelector('h1')?.textContent === 'Settings'`);
  const paused = await Promise.race([request, delay(10000).then(() => { throw new Error('Relay settings load was not intercepted'); })]);
  assert.equal(await connection.read(`document.querySelector('fieldset').disabled`), true);
  assert.equal(await connection.read(`document.querySelector('fieldset input').matches(':disabled')`), true);
  assert.equal(await connection.read(`document.body.textContent.includes('Loading saved relay settings…')`), true);
  await connection.call('Fetch.continueRequest', { requestId: paused.requestId });
  await connection.call('Fetch.disable');
  connection.events.delete('Fetch.requestPaused');
  await connection.until(`document.querySelector('fieldset')?.disabled === false`);
}

async function verify(connection, report) {
  const fixture = JSON.parse(await readFile(resolve('build/ui-fixture.json'), 'utf8'));
  report.source_sha256 = fixture.source_sha256;
  report.frontend_image_id = fixture.frontend_image_id;
  await connection.call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false });
  await connection.call('Page.enable');
  await connection.call('Page.navigate', { url: 'http://localhost:8765' });
  await connection.until(`document.querySelector('h1')?.textContent === 'Welcome back'`);
  assert.equal(await connection.read(`document.querySelector('input[type=password]').type`), 'password');
  await connection.screenshot('login');
  await connection.fill('Master password', fixture.password);
  await connection.click({ tag: 'button', name: 'Continue securely' });
  await connection.until(`document.querySelector('h1')?.textContent === 'One more step'`);
  assert.equal(await connection.read(`document.querySelector('input[type=password]') === null`), true);
  await connection.fill('Digit 1 of 6', fixture.totp);
  await connection.click({ tag: 'button', name: 'Verify and sign in' });
  await connection.until(`document.querySelector('h1')?.textContent === 'Mailboxes'`);
  assert.equal(await connection.read(`document.querySelectorAll('.desktop-table tbody tr').length`), 8);
  assert.equal(await connection.read(`document.cookie.includes('mail_admin')`), false);
  await responsive(connection);
  await connection.click({ tag: 'button', name: 'New mailbox' });
  await connection.until(`document.querySelector('[role=dialog]') !== null`);
  await connection.screenshot('new-mailbox');
  await connection.click({ tag: 'button', name: 'Close' });
  await connection.until(`document.querySelector('[role=dialog]') === null`);
  await mailboxFlows(connection, fixture);
  await openSettings(connection);
  await relayFlows(connection);
  await connection.screenshot('settings');
  await connection.click({ tag: 'button', name: 'Toggle dark mode' });
  await connection.screenshot('settings-dark');
  await connection.click({ tag: 'button', name: 'Sign out' });
  await connection.until(`document.querySelector('h1')?.textContent === 'Welcome back'`);
  assert.equal(await connection.read(`document.querySelectorAll('[role=dialog]').length`), 0);
}

const profile = await mkdtemp(`${tmpdir()}/mail-admin-browser-`);
const reservation = createServer();
await new Promise((resolve, reject) => { reservation.once('error', reject); reservation.listen(8765, '127.0.0.1', resolve); });
await new Promise(resolve => reservation.close(resolve));
const server = spawn(resolve('.venv/bin/python'), ['tools/serve_fixture.py'], { stdio: 'ignore' });
for (let attempt = 0; attempt < 100; attempt++) {
  if (server.exitCode !== null) throw new Error('Synthetic fixture server failed');
  try { if ((await fetch('http://localhost:8765/api/status')).ok) break; }
  catch (error) { if (attempt === 99) throw error; }
  await delay(50);
}
const executable = process.env.MAIL_ADMIN_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const chrome = spawn(executable, ['--headless', '--remote-debugging-port=0', `--user-data-dir=${profile}`, '--no-first-run', '--disable-background-networking', '--disable-sync', 'about:blank'], { stdio: 'ignore' });
let connection;
const report = { scope: 'local synthetic UI checks; not HTTPS or release qualification', started_at_utc: new Date().toISOString(), result: 'fail' };
try {
  connection = await connect(profile);
  await verify(connection, report);
  report.result = 'pass';
} catch (error) {
  if (connection) await connection.screenshot('failure');
  report.reason = error.message;
  process.exitCode = 1;
} finally {
  connection?.socket.close();
  chrome.kill('SIGTERM');
  await new Promise(resolve => chrome.once('exit', resolve));
  server.kill('SIGTERM');
  await new Promise(resolve => server.once('exit', resolve));
  await rm(profile, { recursive: true, force: true });
  report.completed_at_utc = new Date().toISOString();
  await writeFile(resolve('build/browser-checks.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report));
}
