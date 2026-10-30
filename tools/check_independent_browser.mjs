// Functional UI checks against an explicitly isolated two-host simulation.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import { X509Certificate, createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { connect } from './browser_connection.mjs';
const root = resolve(process.argv[2] || 'build/split-simulation/run-01');
const state = JSON.parse(await readFile(`${root}/state.json`, 'utf8'));
assert.equal(state.scope, 'docker-contained-kvm-simulation; not production qualification');
const config = JSON.parse(await readFile(`${root}/workload-config.json`, 'utf8'));
assert.equal(config.scope, 'isolated-two-host-mail-qualification');
const pins = await Promise.all(['management', 'webmail'].map(async kind => {
  const certificate = new X509Certificate(await readFile(`${root}/tls/${kind}.pem`));
  return createHash('sha256').update(certificate.publicKey.export({format:'der',type:'spki'})).digest('base64');
}));
const profile = await mkdtemp(`${tmpdir()}/split-mail-browser-`);
const chrome = spawn(process.env.MAIL_ADMIN_CHROME || '/opt/google/chrome/chrome', [
  '--headless', '--remote-debugging-port=0', `--user-data-dir=${profile}`, '--no-first-run',
  '--no-proxy-server', '--disable-background-networking', '--disable-sync',
  `--host-resolver-rules=MAP manage.split.test ${state.addresses.management},MAP mail.split.test ${state.addresses.management},MAP webmail.split.test ${state.addresses.webmail}`,
  `--ignore-certificate-errors-spki-list=${pins.join(',')}`, 'about:blank',
], {stdio:'ignore'});
const report = {scope:state.scope, result:'fail', cases:[]};
let c;
try {
  c = await connect(profile);
  await c.call('Page.enable'); await c.call('Network.enable');
  for (const kind of ['management','webmail']) {
    const origin = kind === 'management' ? config.management_origin : config.origin;
    const cookie = (await readFile(`${root}/${kind}-cookie`, 'utf8')).trim();
    await c.call('Network.setCookie', {name:'__Host-mail_admin', value:cookie, url:origin, secure:true, httpOnly:true, sameSite:'Strict', path:'/'});
    await c.call('Page.navigate', {url:origin+(kind === 'management' ? '/settings' : '/')});
    await c.until(`document.body?.textContent.includes('Paired application')`);
    assert.equal(await c.read(`document.body.textContent.includes('Deleted mailboxes')`), false);
    assert.equal(await c.read(`[...document.querySelectorAll('button')].some(x=>x.textContent==='Forwarding')`), false);
    for (const width of [360,768,1280]) {
      await c.call('Emulation.setDeviceMetricsOverride', {width,height:1000,deviceScaleFactor:1,mobile:false});
      assert.equal(await c.read('document.documentElement.scrollWidth <= innerWidth'),true);
      await c.screenshot(`independent-${kind}-${width}`);
    }
    const code = await c.read(`fetch('/api/webmail/folders').then(r=>r.status)`);
    assert.equal(code,kind === 'management' ? 404 : 403);
    report.cases.push(`${kind}: pairing and recovery controls, administrator mailbox denial, responsive administration`);
  }
  await c.call('Network.clearBrowserCookies');
  await c.call('Page.navigate', {url:config.origin});
  await c.until(`document.querySelector('h1')?.textContent==='Welcome back'`);
  await c.fill('Email address', config.accounts[0]); await c.fill('Password', config.password);
  await c.click({tag:'button',name:'Continue securely'});
  await c.until(`document.querySelector('h1')?.textContent==='Your mail'`);
  await c.until(`document.querySelector('[aria-label="Messages"]')!==null`);
  for (const width of [360,768,1280]) {
    await c.call('Emulation.setDeviceMetricsOverride', {width,height:1000,deviceScaleFactor:1,mobile:false});
    assert.equal(await c.read('document.documentElement.scrollWidth <= innerWidth'),true);
    await c.screenshot(`independent-inbox-${width}`);
  }
  assert.equal(await c.read('localStorage.length+sessionStorage.length'),0);
  report.cases.push('mailbox login over verified IMAP, responsive real inbox, no browser credential storage');
  await c.click({tag:'button',name:'Sign out'});
  await c.until(`document.querySelector('h1')?.textContent==='Welcome back'`);
  report.result='pass';
} catch(error) {
  report.reason=error.message;process.exitCode=1;
  if(c) await c.screenshot('independent-failure');console.error(error.stack);
} finally {
  c?.socket.close();chrome.kill('SIGTERM');await new Promise(resolve=>chrome.once('exit',resolve));
  await rm(profile,{recursive:true,force:true});
  await writeFile(`${root}/browser.json`,JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}
