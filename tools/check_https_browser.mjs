// Existing workstation Chrome, the compiled application, real HTTPS and Unix transport.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { connect } from './browser_connection.mjs';

const directory = resolve(process.argv[2]);
const material = JSON.parse(await readFile(join(directory, 'material.json'), 'utf8'));
const profile = await mkdtemp(join(tmpdir(), 'mail-admin-https-browser-'));
const credentials = [material.password, material.relay_key, material.mailbox_password, ...material.codes];
const codes = [...material.codes];
const report = { started_at_utc: new Date().toISOString(), result: 'fail', assertions: {} };
const executable = process.env.MAIL_ADMIN_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const chrome = spawn(executable, [
  '--headless', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
  '--no-first-run', '--disable-background-networking', '--disable-sync', '--no-proxy-server',
  '--host-resolver-rules=MAP mail.rsalehin24.me 127.0.0.1',
  `--ignore-certificate-errors-spki-list=${material.spki}`, 'about:blank',
], { stdio: 'ignore' });

async function login(connection) {
  await connection.until(`document.querySelector('h1')?.textContent === 'Welcome back'`);
  assert.equal(await connection.read(`document.querySelector('input[type=password]').type`), 'password');
  await connection.fill('Master password', material.password);
  await connection.click({ tag: 'button', name: 'Continue securely' });
  await connection.until(`document.querySelector('h1')?.textContent === 'One more step'`);
  await connection.click({ tag: 'button', name: 'Use a recovery code' });
  await connection.fill('Recovery code', codes.pop());
  await connection.click({ tag: 'button', name: 'Verify and sign in' });
  await connection.until(`document.querySelector('h1')?.textContent === 'Mailboxes'`);
  await connection.until(`document.body.textContent.includes('Host telemetry observed')`);
}

async function reauthenticate(connection) {
  await connection.until(`document.querySelector('#reauth-title') !== null`);
  await connection.fill('Master password', material.password);
  await connection.click({ tag: 'button', name: 'Verify password' });
  await connection.until(`[...document.querySelectorAll('button')].some(button => button.textContent === 'Recovery code')`);
  await connection.click({ tag: 'button', name: 'Recovery code' });
  await connection.fill('Recovery code', codes.pop());
  await connection.click({ tag: 'button', name: 'Verify second factor' });
  await connection.until(`document.querySelector('#reauth-title') === null`);
}

async function api(connection, path) {
  return connection.read(`(async () => {
    const response = await fetch(${JSON.stringify(path)}, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}', cache: 'no-store',
    });
    return {status: response.status, body: await response.json()};
  })()`);
}

async function inertMailbox(connection) {
  const text = '<img src=x onerror=window.__mailBrowserInjected=true>';
  await connection.click({ tag: 'button', name: 'New mailbox' });
  await connection.until(`document.querySelector('[role=dialog]') !== null`);
  await connection.fill('Username prefix', 'https-browser');
  await connection.fill('Display name', text);
  await connection.fill('Notes / purpose', text);
  await connection.fill('Incoming mailbox password', material.mailbox_password);
  assert.equal(await connection.read(`document.querySelector('input[autocomplete="new-password"]').type`), 'password');
  await connection.click({ tag: 'button', name: 'Create mailbox' });
  await connection.until(`document.querySelector('[role=dialog]') === null && document.querySelectorAll('.desktop-table tbody tr').length === 1`);
  assert.equal(await connection.read(`document.body.textContent.includes(${JSON.stringify(text)})`), true);
  assert.equal(await connection.read(`Boolean(window.__mailBrowserInjected) || Boolean(document.querySelector('img[src=x]'))`), false);
  report.assertions.untrusted_text_inert = true;
}

async function relay(connection) {
  await connection.click({ tag: 'button', name: 'Settings' });
  await connection.until(`document.querySelector('fieldset')?.disabled === false`);
  await connection.fill('Brevo SMTP login', 'synthetic-browser-login');
  await connection.fill('Brevo SMTP key', material.relay_key);
  assert.equal(await connection.read(`document.querySelector('input[autocomplete="new-password"]').type`), 'password');
  await connection.click({ tag: 'button', name: 'Save relay' });
  await reauthenticate(connection);
  await connection.until(`document.body.textContent.includes('Saved revision 1')`);
  assert.equal(await connection.read(`document.querySelector('input[autocomplete="new-password"]').value`), '');
  await connection.call('Page.reload', { ignoreCache: true });
  await connection.until(`document.querySelector('h1')?.textContent === 'Mailboxes'`);
  report.assertions.session_survives_reload = true;
  assert.equal(await connection.read(`document.body.textContent.includes(${JSON.stringify(material.relay_key)})`), false);
  await connection.click({ tag: 'button', name: 'Sign out' });
  await login(connection);
  const diagnostics = await connection.read(`fetch('/api/diagnostics', {cache: 'no-store'}).then(async response => ({status: response.status, body: await response.json()}))`);
  assert.equal(diagnostics.status, 200);
  assert.equal(diagnostics.body.dns_mx.state, 'unknown');
  report.assertions.isolated_dns_unavailable = true;
  const denied = await api(connection, '/api/settings/relay/reveal');
  assert.equal(denied.status, 403);
  assert.equal(denied.body.error, 'recent_authentication_required');
  report.assertions.reveal_requires_recent_auth = true;
  await connection.click({ tag: 'button', name: 'Configure' });
  await connection.until(`document.querySelector('#client-title') !== null`);
  assert.equal(await connection.read(`document.querySelector('[role=dialog]').textContent.includes(${JSON.stringify(material.relay_key)})`), false);
  await connection.click({ tag: 'button', name: 'Reveal outgoing key' });
  await reauthenticate(connection);
  // Reauthentication closes before the resumed reveal request completes.
  await connection.until(`document.querySelector('[role=dialog]')?.textContent.includes(${JSON.stringify(material.relay_key)})`);
  await connection.click({ tag: 'button', name: 'Close' });
  assert.equal(await connection.read(`document.body.textContent.includes(${JSON.stringify(material.relay_key)})`), false);
  report.assertions.password_fields_masked = true;
}

async function safeErrors(connection) {
  const failure = await api(connection, '/api/accounts/create');
  assert.equal(failure.status, 422);
  assert.deepEqual(Object.keys(failure.body).sort(), ['error', 'request_id']);
  assert.equal(failure.body.error, 'invalid_request');
  assert.match(failure.body.request_id, /^[0-9a-f]{8}-[0-9a-f-]{27}$/);
  report.assertions.errors_have_safe_ids = true;
}

async function persistence(connection) {
  assert.equal(await connection.read('localStorage.length + sessionStorage.length'), 0);
  assert.deepEqual(await connection.read('(async () => (await indexedDB.databases()).map(value => value.name))()'), []);
  assert.deepEqual(await connection.read('(async () => await caches.keys())()'), []);
  assert.equal(await connection.read(`document.cookie.includes('mail_admin')`), false);
  const cookies = (await connection.call('Storage.getCookies')).cookies;
  const session = cookies.find(cookie => cookie.name === '__Host-mail_admin');
  assert.ok(session && session.secure && session.httpOnly && session.sameSite === 'Strict');
  assert.equal(session.domain, 'mail.rsalehin24.me');
  report.assertions.http_only_cookie = true;
}

async function profileHasSecret(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory() && await profileHasSecret(path)) return true;
    if (entry.isFile()) {
      const bytes = await readFile(path);
      if (credentials.some(value => bytes.includes(Buffer.from(value)))) return true;
    }
  }
  return false;
}

let connection;
const responses = [], consoleOutput = [];
try {
  connection = await connect(profile);
  await connection.call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false });
  await connection.call('Page.enable');
  await connection.call('Network.enable');
  await connection.call('Runtime.enable');
  connection.events.set('Network.responseReceived', event => {
    if (event.response.url.startsWith('https://mail.rsalehin24.me/api/')) responses.push(event.response);
  });
  connection.events.set('Runtime.consoleAPICalled', event => consoleOutput.push(JSON.stringify(event.args)));
  await connection.call('Page.navigate', { url: 'https://mail.rsalehin24.me' });
  await login(connection);
  await inertMailbox(connection);
  await relay(connection);
  await safeErrors(connection);
  await persistence(connection);
  assert.ok(responses.length >= 10);
  assert.ok(responses.every(response => Object.entries(response.headers).some(([key, value]) => key.toLowerCase() === 'cache-control' && value.includes('no-store'))));
  assert.ok(responses.some(response => response.securityDetails?.protocol.startsWith('TLS')));
  assert.ok(credentials.every(value => !consoleOutput.join('').includes(value)));
  report.assertions.no_store_api = true;
  report.api_responses_observed = responses.length;
  report.result = 'pass';
} catch (error) {
  report.reason = credentials.reduce((message, value) => message.replaceAll(value, '[redacted]'), String(error.message));
  if (connection) {
    const text = await connection.read('document.body.innerText');
    report.page = credentials.reduce((message, value) => message.replaceAll(value, '[redacted]'), text);
  }
  process.exitCode = 1;
} finally {
  connection?.socket.close();
  if (chrome.exitCode === null) {
    chrome.kill('SIGTERM');
    await new Promise(resolve => chrome.once('exit', resolve));
  }
  try {
    assert.equal(await profileHasSecret(profile), false, 'A secret persisted in the browser profile');
    report.assertions.secret_browser_persistence_absent = true;
  } catch (error) {
    report.result = 'fail';
    report.reason = error.message;
    process.exitCode = 1;
  }
  await rm(profile, { recursive: true, force: true });
  report.completed_at_utc = new Date().toISOString();
  await writeFile(join(directory, 'node-observation.json'), JSON.stringify(report, null, 2) + '\n', { mode: 0o600 });
}
