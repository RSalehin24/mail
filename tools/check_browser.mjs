// Standalone local Chrome fallback: synthetic fixture only, no additional packages.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { createServer } from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';

import { connect } from './browser_connection.mjs';

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

async function recoveryControls(connection) {
  await connection.click({ tag: 'a', name: 'RSalehin24 Mail Admin home' });
  await connection.until(`document.querySelector('h1')?.textContent === 'Mailboxes'`);
  await connection.until(`document.querySelector('a[href="/mailboxes/cloud%40rsalehin24.me/details"]') !== null`);
  await connection.click({ tag: 'a', name: 'cloud@rsalehin24.me' });
  await connection.until(`document.querySelector('[role=tab]')?.textContent === 'Details'`);
  await connection.click({ tag: 'button', name: 'Configuration' });
  await connection.until(`document.querySelector('h1')?.textContent === 'cloud@rsalehin24.me'`);
  await connection.click({ tag: 'button', name: 'Delete mailbox' });
  await connection.until(`document.querySelector('[role=dialog] [role=combobox]') !== null`);
  await connection.click({ tag: '[role=combobox]', name: 'Retain for 7 days' });
  await connection.until(`document.querySelector('[role=listbox]') !== null`);
  assert.deepEqual(await connection.read(`[...document.querySelectorAll('[role=option]')].map(option => option.textContent)`), ['Retain for 7 days', 'Retain for 30 days', 'Keep forever', 'Don’t Retain']);
  await connection.read(`[...document.querySelectorAll('[role=option]')].find(option => option.textContent === 'Keep forever').click()`);
  await connection.until(`document.querySelector('[role=listbox]') === null`);
  assert.equal(await connection.read(`[...document.querySelectorAll('[role=dialog] button')].find(button => button.textContent === 'Delete mailbox').disabled`), true);
  await connection.fill('Type the mailbox address to confirm', 'wrong@rsalehin24.me');
  assert.equal(await connection.read(`[...document.querySelectorAll('[role=dialog] button')].find(button => button.textContent === 'Delete mailbox').disabled`), true);
  await connection.screenshot('recovery-delete-dialog');
  await connection.click({ tag: 'button', name: 'Cancel' });
  await connection.until(`document.querySelector('[role=dialog]') === null`);
  await connection.click({ tag: 'button', name: 'Deleted' });
  await connection.until(`document.querySelector('h1')?.textContent === 'Deleted Mailboxes' && document.body.textContent.includes('No deleted mailboxes.')`);
  await connection.call('Page.reload');
  await connection.until(`document.querySelector('h1')?.textContent === 'Deleted Mailboxes'`);
  await connection.click({ tag: 'button', name: 'Protected Mailboxes' });
  await connection.until(`document.querySelector('input')?.closest('section')?.textContent.includes('Protected Mailboxes')`);
  assert.equal(await connection.read(`document.querySelector('title').textContent`), 'RSalehin24 Mail');
}

async function settingsTabs(connection) {
  await connection.click({ tag: 'button', name: 'Settings' });
  assert.deepEqual(await connection.read(`[...document.querySelectorAll('[role=tab]')].map(tab => tab.textContent)`), ['Security', 'Outgoing mail provider', 'Backup & Recovery']);
  await connection.call('Page.navigate', { url: 'http://localhost:8765/settings?tab=protected' });
  await connection.until(`location.pathname === '/deleted'`);
  assert.deepEqual(await connection.read(`[...document.querySelectorAll('[role=tab]')].map(tab => tab.textContent)`), ['Configure', 'Protected Mailboxes', 'Deleted Mailboxes']);
  await connection.click({ tag: 'button', name: 'Protected Mailboxes' });
  await connection.until(`document.querySelector('input')?.closest('section')?.textContent.includes('Protected Mailboxes')`);
  assert.equal(await connection.read(`document.querySelectorAll('[aria-label="Go to page 2"]').length > 0`), true);
  assert.equal(await connection.read(`document.body.textContent.includes('Enable mailbox deletion')`), false);
  assert.equal(await connection.read(`Boolean([...document.querySelectorAll('input')].find(input => input.labels?.[0]?.textContent === 'Mailbox to protect').compareDocumentPosition(document.querySelector('.desktop-table')) & Node.DOCUMENT_POSITION_FOLLOWING)`), true);
  await connection.read(`document.querySelector('.desktop-table tbody tr button').click()`);
  await connection.until(`document.activeElement?.labels?.[0]?.textContent === 'Protection reason'`);
  assert.equal(await connection.read(`[...document.querySelectorAll('input')].find(input => input.labels?.[0]?.textContent === 'Mailbox to protect').disabled`), true);
  await connection.fill('Protection reason', 'Updated protection reason');
  await connection.click({tag: 'button', name: 'Save reason'});
  await connection.until(`document.querySelector('.desktop-table tbody tr').textContent.includes('Updated protection reason')`);
  await connection.until(`document.body.textContent.includes('Protection reason saved.')`);
  assert.equal(await connection.read(`document.body.textContent.includes('Save protections')`), false);

  await connection.call('Page.reload');
  await connection.until(`document.querySelector('[role=tab][aria-selected=true]')?.textContent === 'Protected Mailboxes'`);
  await connection.until(`document.querySelector('.desktop-table tbody tr')?.textContent.includes('Updated protection reason')`);
  await connection.fill('Mailbox to protect', 'single-action@rsalehin24.me');
  await connection.fill('Protection reason', 'Single action protection');
  await connection.click({tag: 'button', name: 'Add protection'});
  await connection.until(`document.body.textContent.includes('Mailbox protection added.')`);
  await connection.call('Page.reload');
  await connection.until(`document.querySelector('h1')?.textContent === 'Deleted Mailboxes' && document.querySelector('[aria-label="Go to page 2"]') !== null`);
  await connection.click({tag: 'button', name: 'Go to page 2'});
  await connection.until(`document.querySelector('.desktop-table tbody')?.textContent.includes('single-action@')`);
  await connection.read(`[...document.querySelectorAll('.desktop-table tbody tr')].find(row => row.textContent.includes('single-action@')).querySelector('[aria-label^="Remove protection"]').click()`);
  await connection.until(`document.querySelector('#remove-protection-title') !== null`);
  assert.equal(await connection.read(`[...document.querySelectorAll('[role=dialog] button')].find(button => button.textContent === 'Remove protection').disabled`), true);
  await connection.click({tag: 'button', name: 'Cancel'});
  assert.equal(await connection.read(`document.querySelector('.desktop-table tbody').textContent.includes('single-action@')`), true);
  await connection.read(`[...document.querySelectorAll('.desktop-table tbody tr')].find(row => row.textContent.includes('single-action@')).querySelector('[aria-label^="Remove protection"]').click()`);
  await connection.click({tag: 'input', name: 'I understand the consequences of removing this protection.'});
  await connection.click({tag: 'button', name: 'Remove protection'});
  await connection.until(`document.querySelector('[role=dialog]') === null && document.body.textContent.includes('Mailbox protection removed.')`);
  await connection.call('Page.reload');
  await connection.until(`document.querySelector('.desktop-table tbody')?.textContent.includes('Updated protection reason')`);

  await connection.until(`document.querySelector('[aria-label="Go to page 2"]') !== null`);
  await connection.click({ tag: 'button', name: 'Go to page 2' });
  await connection.until(`document.body.textContent.includes('protected13@rsalehin24.me')`);
  await connection.click({ tag: 'button', name: 'Go to first page' });
  for (const dark of [false, true]) {
    if (dark) await connection.click({ tag: 'button', name: 'Toggle dark mode' });
    for (const width of [360, 768, 1280]) {
      await connection.call('Emulation.setDeviceMetricsOverride', { width, height: 1000, deviceScaleFactor: 1, mobile: false });
      await connection.screenshot(`protected-mailboxes-${dark ? 'dark' : 'light'}-${width}`);
      assert.equal(await connection.read('document.documentElement.scrollWidth <= innerWidth'), true);
    }
  }
  await connection.click({ tag: 'button', name: 'Toggle dark mode' });
  await connection.call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false });
  await connection.click({ tag: 'button', name: 'Configure' });
  await connection.until(`document.body.textContent.includes('Enable mailbox deletion')`);
  assert.equal(await connection.read(`document.querySelector('input[aria-label^="Remove protection"]') !== null || document.body.textContent.includes('Mailbox to protect')`), false);
  await connection.click({ tag: 'button', name: 'Settings' });
  await connection.click({ tag: 'button', name: 'Backup & Recovery' });
  await connection.until(`document.querySelector('[role=tab][aria-selected=true][id=backup-section-backups]') !== null`);
  await connection.until(`document.querySelector('.MuiPaginationItem-ellipsis') !== null`);
  assert.equal(await connection.read(`document.body.textContent.includes('3:00 AM · Asia/Dhaka · 7-day retention')`), true);
  assert.equal(await connection.read(`document.querySelector('.MuiTablePagination-root select') !== null`), true);
  await connection.click({ tag: 'button', name: 'Logs' });
  await connection.until(`document.body.textContent.includes('Retry backup')`);
  for (const dark of [false, true]) {
    if (dark) await connection.click({ tag: 'button', name: 'Toggle dark mode' });
    for (const width of [360, 768, 1280]) {
      await connection.call('Emulation.setDeviceMetricsOverride', { width, height: 1000, deviceScaleFactor: 1, mobile: false });
      await connection.screenshot(`backup-settings-${dark ? 'dark' : 'light'}-${width}`);
      assert.equal(await connection.read('document.documentElement.scrollWidth <= innerWidth'), true);
    }
  }
  await connection.click({ tag: 'button', name: 'Toggle dark mode' });
  await connection.call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false });
  await connection.call('Page.reload');
  await connection.until(`document.querySelector('[role=tab][aria-selected=true]')?.textContent === 'Backup & Recovery'`);
  await connection.call('Fetch.enable', {patterns: [{urlPattern: '*/api/backups?kind=operations*', requestStage: 'Request'}]});
  connection.events.set('Fetch.requestPaused', event => { void connection.call('Fetch.fulfillRequest', {requestId: event.requestId, responseCode: 200, responseHeaders: [{name: 'Content-Type', value: 'application/json'}], body: Buffer.from(JSON.stringify({items: [{id: 'complete-operation', kind: 'backup', snapshot: '', state: 'complete', phase: 'complete', created: 1700000000, updated: 1700000100, error: '', result: {}}], total: 1, page: 1, page_size: 10, busy: false, observation: null})).toString('base64')}); });
  await connection.call('Page.reload');
  await connection.until(`document.querySelector('.desktop-table tbody')?.textContent.includes('complete-operation')`);
  assert.deepEqual(await connection.read(`[...document.querySelectorAll('.desktop-table th')].map(cell => cell.textContent)`), ['Started', 'Operation', 'Status', 'Updated']);
  await connection.call('Fetch.disable');
  connection.events.delete('Fetch.requestPaused');

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
  await connection.click({ tag: 'button', name: 'Actions for ui-fixture@rsalehin24.me' });
  await connection.click({ tag: 'button', name: 'Mailbox settings' });
  await connection.until(`document.querySelector('#client-title') !== null`);
  for (const name of ['Apple Mail', 'Gmail', 'Thunderbird / Outlook', 'Nextcloud', 'Raw details']) {
    await connection.click({ tag: 'button', name });
    assert.equal(await connection.read(`[...document.querySelectorAll('[role=tab][aria-selected=true]')].at(-1).textContent`), name);
  }
  assert.equal(await connection.read(`document.body.textContent.includes('hashes')`), true);
  assert.equal(await connection.read('location.pathname'), '/mailboxes/ui-fixture%40rsalehin24.me/connections');
  await connection.click({ tag: 'button', name: 'Configuration' });
  await connection.until(`document.body.textContent.includes('Change incoming password')`);
  await connection.click({ tag: 'button', name: 'Change incoming password' });
  await connection.fill('Incoming mailbox password', '  replacement fixture password 42  ');
  await connection.click({ tag: 'button', name: 'Reset password' });
  await connection.until(`document.querySelector('#reauth-title') !== null`);
  await connection.fill('Master password', 'incorrect password');
  await connection.click({ tag: 'button', name: 'Verify password' });
  await connection.until(`[...document.querySelectorAll('[role=alert]')].some(element => element.textContent.includes('Invalid master password.'))`);
  assert.equal(await connection.read(`document.querySelector('#reauth-title').closest('[role=dialog]').querySelector('[role=alert]').textContent`), 'Invalid master password.');
  assert.equal(await connection.read(`document.querySelector('#reauth-title') !== null`), true);
  await connection.fill('Master password', fixture.password);
  await connection.click({ tag: 'button', name: 'Verify password' });
  await connection.until(`[...document.querySelectorAll('button')].some(button => button.textContent === 'Recovery code')`);
  await connection.click({ tag: 'button', name: 'Recovery code' });
  await connection.fill('Recovery code', fixture.recovery_code);
  await connection.click({ tag: 'button', name: 'Verify second factor' });
  await connection.until(`document.querySelector('[role=dialog]') === null`);
  assert.equal(await connection.read('localStorage.length + sessionStorage.length'), 0);
  await connection.fill('Display name', 'Updated mailbox');
  await connection.click({ tag: 'button', name: 'Save details' });
  await connection.until(`document.querySelector('h1')?.nextElementSibling?.textContent === 'Updated mailbox'`);
  await connection.call('Page.reload');
  await connection.until(`document.querySelector('h1')?.textContent === 'ui-fixture@rsalehin24.me'`);
  await connection.until(`document.querySelector('input[value="Updated mailbox"]') !== null`);
  await connection.click({ tag: 'button', name: 'Details' });
  await connection.until(`location.pathname.endsWith('/details')`);
  await connection.read('history.back()');
  await connection.until(`location.pathname.endsWith('/configuration')`);
  await connection.read('history.forward()');
  await connection.until(`location.pathname.endsWith('/details')`);
  await connection.click({ tag: 'a', name: 'RSalehin24 Mail Admin home' });
  await connection.until(`document.querySelector('h1')?.textContent === 'Mailboxes'`);
  await connection.until(`document.body.textContent.includes('Updated mailbox')`);
  assert.equal(await connection.read(`getComputedStyle(document.querySelector('thead th:nth-child(3)')).textAlign`), 'center');
  assert.equal(await connection.read(`document.querySelector('.stat-row') === null`), true);
  assert.deepEqual(await connection.read(`[...document.querySelectorAll('.desktop-table th')].map(cell => cell.textContent.trim()).filter(Boolean)`), ['Name', 'Mailbox', 'Creation Date', 'Inbox', 'Sending', 'Actions']);
  assert.equal(await connection.read(`Boolean(document.querySelector('.MuiPagination-root [aria-label="Go to first page"]')) && Boolean(document.querySelector('[aria-label="Go to last page"]'))`), true);

  assert.match(await connection.read(`document.querySelector('.desktop-table tbody time').textContent`), /^[A-Z][a-z]{2} \d{1,2}, \d{4}$/);

  const favicon = await fetch('http://localhost:8765/favicon.svg');
  assert.equal(favicon.headers.get('content-type').includes('image/svg+xml'), true);
  assert.equal((await favicon.text()).startsWith('<svg'), true);
  for (const dark of [false, true]) {
    if (dark) await connection.click({ tag: 'button', name: 'Toggle dark mode' });
    for (const width of [360, 768, 1280]) {
      await connection.call('Emulation.setDeviceMetricsOverride', { width, height: 1000, deviceScaleFactor: 1, mobile: false });
      await connection.screenshot(`redesign-${dark ? 'dark' : 'light'}-${width}`);
      assert.equal(await connection.read('document.documentElement.scrollWidth <= innerWidth'), true);
    }
  }
  await connection.click({ tag: 'button', name: 'Toggle dark mode' });
}

async function relayFlows(connection) {
  await connection.click({ tag: 'button', name: 'Outgoing mail provider' });
  await connection.fill('Brevo SMTP login', 'synthetic-browser-login');
  await connection.fill('Brevo SMTP key', 'synthetic-browser-key');
  await connection.click({ tag: 'button', name: 'Save relay' });
  await connection.until(`document.body.textContent.includes('Saved revision 1')`);
  assert.equal(await connection.read(`document.querySelector('input[autocomplete="new-password"]').value`), '');
  await connection.click({ tag: 'button', name: 'Test connection' });
  await connection.until(`document.body.textContent.includes('Connection only · no email sent')`);
  assert.equal(await connection.read(`document.body.textContent.includes('Administrator email delivery needs verification')`), true);
  await connection.click({ tag: 'button', name: 'Refresh provider credits' });
  await connection.until(`document.body.textContent.includes('No API key configured')`);
  assert.equal(await connection.read('localStorage.length + sessionStorage.length'), 0);
}

async function verificationFlows(connection) {
  await connection.click({ tag: 'a', name: 'RSalehin24 Mail Admin home' });
  await connection.until(`document.querySelector('h1')?.textContent === 'Mailboxes'`);
  await connection.until(`document.querySelector('input[aria-label="Select cloud@rsalehin24.me"]') !== null`);
  await connection.click({ tag: 'input', name: 'Select cloud@rsalehin24.me' });
  await connection.click({ tag: 'input', name: 'Select mail@rsalehin24.me' });
  await connection.click({ tag: 'button', name: 'Verify selected senders' });
  await connection.click({ tag: 'button', name: 'Send 2 test emails' });
  await connection.until(`document.querySelectorAll('[role=dialog] button').length > 0 && [...document.querySelectorAll('[role=dialog] button')].filter(button => button.textContent === 'I received this email').length === 2`);
  await connection.read(`[...document.querySelectorAll('[role=dialog] button')].find(button => button.textContent === 'I received this email').click()`);
  await connection.until(`[...document.querySelectorAll('[role=dialog] button')].filter(button => button.textContent === 'I received this email').length === 1`);
  await connection.click({ tag: 'button', name: 'Done' });
  await connection.until(`document.querySelector('[role=dialog]') === null`);
  await connection.click({ tag: 'button', name: 'Settings' });
  await connection.click({ tag: 'button', name: 'Outgoing mail provider' });
  await connection.until(`document.body.textContent.includes('Administrator email delivery needs verification')`);
}

async function securityAndServer(connection) {
  await connection.click({ tag: 'button', name: 'Security' });
  const headings = await connection.read(`[...document.querySelectorAll('#security-heading ~ div h3')].map(element => element.textContent)`);
  assert.deepEqual(headings, ['Master password', 'Authenticator app', 'Recovery codes']);
  await connection.read(`document.querySelector('#security-heading').scrollIntoView({block: 'start'})`);
  await connection.screenshot('security-cards');
  await connection.click({ tag: 'button', name: 'Change password' });
  await connection.until(`document.querySelector('#master-password-form') !== null`);
  await connection.fill('New password', 'synthetic new password for layout');
  await connection.fill('Confirm new password', 'different password');
  assert.equal(await connection.read(`[...document.querySelectorAll('[role=dialog] button')].find(button => button.textContent === 'Save new password').disabled`), true);
  await connection.click({ tag: 'button', name: 'Cancel' });
  await connection.until(`document.querySelector('[role=dialog]') === null`);
  await connection.call('Network.enable');
  const active = new Set(), times = []; let maximum = 0;
  connection.events.set('Network.requestWillBeSent', event => {
    if (new URL(event.request.url).pathname === '/api/telemetry') { active.add(event.requestId); times.push(Date.now()); maximum = Math.max(maximum, active.size); }
  });
  connection.events.set('Network.loadingFinished', event => active.delete(event.requestId));
  connection.events.set('Network.loadingFailed', event => active.delete(event.requestId));
  await connection.click({ tag: 'button', name: 'Server' });
  await connection.until(`Number(document.querySelector('[data-sample-count]')?.getAttribute('data-sample-count')) >= 4`);
  assert.equal(await connection.read(`[...document.querySelectorAll('[aria-pressed=true]')].some(button => button.textContent === '30 seconds')`), true);
  assert.equal(maximum, 1, 'Telemetry requests never overlap');
  assert.ok(times.length >= 4);
  assert.ok(times.slice(2).every((value, index) => value - times[index + 1] >= 700 && value - times[index + 1] <= 1800), 'Steady resource polling targets one second');
  assert.equal(await connection.read(`document.querySelectorAll('svg[role=img]').length >= 3`), true);
  for (const dark of [false, true]) {
    if (dark) await connection.click({ tag: 'button', name: 'Toggle dark mode' });
    for (const width of [360, 768, 1280]) {
      await connection.call('Emulation.setDeviceMetricsOverride', { width, height: 1100, deviceScaleFactor: 1, mobile: false });
      await connection.read('scrollTo(0,0)'); await delay(300);
      assert.equal(await connection.read('document.documentElement.scrollWidth <= innerWidth'), true);
      await connection.screenshot(`server-charts-${dark ? 'dark' : 'light'}-${width}`);
    }
  }
  await connection.click({ tag: 'button', name: 'Toggle dark mode' });
  connection.events.delete('Network.requestWillBeSent'); connection.events.delete('Network.loadingFinished'); connection.events.delete('Network.loadingFailed');
  await connection.click({ tag: 'button', name: 'Settings' });
  await connection.click({ tag: 'button', name: 'Security' });
  await connection.until(`document.querySelector('#security-heading') !== null`);
}

async function concurrentLoadingAndPagination(connection) {
  const paused = [];
  const accounts = Array.from({ length: 120 }, (_, index) => ({
    email: `fixture-${String(index).padStart(3, '0')}@rsalehin24.me`, display_name: `Fixture ${index}`,
    notes: '', created: 1700000000 + index, status: 'ready', credentials_present: true, storage_present: true,
    outbound_readiness: 'not_tested',
  }));
  await connection.call('Fetch.enable', { patterns: [
    { urlPattern: '*/api/accounts', requestStage: 'Request' },
    { urlPattern: '*/api/diagnostics', requestStage: 'Request' },
  ] });
  connection.events.set('Fetch.requestPaused', event => {
    if (new URL(event.request.url).pathname === '/api/accounts') {
      void connection.call('Fetch.fulfillRequest', { requestId: event.requestId, responseCode: 200,
        responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
        body: Buffer.from(JSON.stringify(accounts)).toString('base64') });
    } else paused.push(event.requestId);
  });
  await connection.click({ tag: 'button', name: 'Mailboxes' });
  await connection.until(`document.querySelector('.desktop-table tbody')?.textContent.includes('fixture-119@')`);
  assert.equal(await connection.read(`document.querySelectorAll('.desktop-table tbody tr').length`), 10);
  for (let page = 2; page <= 6; page++) {
    await connection.read(`document.querySelector('.MuiPagination-root [aria-label="Go to next page"]').click()`);
    await connection.until(`document.querySelector('.MuiPagination-root [aria-current=page]')?.textContent === '${page}'`);
  }
  assert.equal(await connection.read(`document.querySelectorAll('.MuiPaginationItem-ellipsis').length`), 2);
  await connection.fill('Search mailboxes', 'fixture-119');
  await connection.until(`document.querySelectorAll('.desktop-table tbody tr').length === 1`);
  assert.equal(await connection.read(`document.querySelector('.MuiPagination-root [aria-current=page]').textContent`), '1');
  await connection.fill('Search mailboxes', '');
  await connection.until(`document.querySelectorAll('.desktop-table tbody tr').length === 10`);
  await connection.click({ tag: 'button', name: 'Server' });
  await connection.until(`Number(document.querySelector('[data-sample-count]')?.getAttribute('data-sample-count')) >= 4`);
  assert.equal(await connection.read(`[...document.querySelectorAll('[aria-pressed=true]')].some(button => button.textContent === '30 seconds')`), true);
  assert.ok(paused.length > 0, 'Diagnostics remain deliberately paused while mailboxes and telemetry render');
  for (const requestId of paused) await connection.call('Fetch.continueRequest', { requestId });
  await connection.call('Fetch.disable'); connection.events.delete('Fetch.requestPaused');
  await connection.click({ tag: 'button', name: 'Settings' });
  await connection.click({ tag: 'button', name: 'Security' });
  await connection.until(`document.querySelector('#security-heading') !== null`);
}

async function openSettings(connection) {
  await connection.call('Fetch.enable', { patterns: [{ urlPattern: '*/api/settings/relay', requestStage: 'Request' }] });
  const request = new Promise(resolve => connection.events.set('Fetch.requestPaused', resolve));
  await connection.click({ tag: 'button', name: 'Settings' });
  await connection.until(`document.querySelector('h1')?.textContent === 'Settings'`);
  await connection.click({ tag: 'button', name: 'Outgoing mail provider' });
  const paused = await Promise.race([request, delay(10000).then(() => { throw new Error('Relay settings load was not intercepted'); })]);
  assert.equal(await connection.read(`document.querySelector('fieldset').disabled`), true);
  assert.equal(await connection.read(`document.querySelector('fieldset input').matches(':disabled')`), true);
  assert.equal(await connection.read(`document.body.textContent.includes('Loading saved relay settings…')`), true);
  const retry = new Promise(resolve => connection.events.set('Fetch.requestPaused', resolve));
  await connection.call('Fetch.fulfillRequest', { requestId: paused.requestId, responseCode: 503, responseHeaders: [{ name: 'Content-Type', value: 'application/json' }, { name: 'Retry-After', value: '1' }], body: Buffer.from(JSON.stringify({ error: 'work_overloaded' })).toString('base64') });
  const retried = await Promise.race([retry, delay(3000).then(() => { throw new Error('Temporary GET overload was not retried'); })]);
  await connection.call('Fetch.continueRequest', { requestId: retried.requestId });
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
  await connection.read(`(() => {const select = document.querySelector('.MuiTablePagination-root select'); select.value = '5'; select.dispatchEvent(new Event('change', {bubbles: true}));})()`);
  await connection.until(`document.querySelectorAll('.desktop-table tbody tr').length === 5`);
  await connection.read(`document.querySelector('.MuiPagination-root [aria-label="Go to next page"]').click()`);
  await connection.until(`document.querySelectorAll('.desktop-table tbody tr').length === 3`);
  await connection.read(`document.querySelector('.MuiPagination-root [aria-label="Go to first page"]').click()`);
  await connection.until(`document.querySelectorAll('.desktop-table tbody tr').length === 5`);
  await connection.read(`(() => {const select = document.querySelector('.MuiTablePagination-root select'); select.value = '10'; select.dispatchEvent(new Event('change', {bubbles: true}));})()`);
  await connection.until(`document.querySelectorAll('.desktop-table tbody tr').length === 8`);
  await connection.read('window.scrollTo(0, 0)');
  await responsive(connection);
  await connection.click({ tag: 'button', name: 'New mailbox' });
  await connection.until(`document.querySelector('[role=dialog]') !== null`);
  await connection.screenshot('new-mailbox');
  await connection.click({ tag: 'button', name: 'Close' });
  await connection.until(`document.querySelector('[role=dialog]') === null`);
  await mailboxFlows(connection, fixture);
  await openSettings(connection);
  await relayFlows(connection);
  await verificationFlows(connection);
  await recoveryControls(connection);
  await settingsTabs(connection);
  await securityAndServer(connection);
  await concurrentLoadingAndPagination(connection);
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
  if (connection) await connection.screenshot(`failure-${Date.now()}`);
  console.error(error.stack);
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
