import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

export class Connection {
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
    const result = await this.call('Runtime.evaluate', {
      expression, returnByValue: true, awaitPromise: true,
    });
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
      const element = candidates[0];
      element.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'});
      const bounds = element.getBoundingClientRect();
      return {count: 1, disabled: Boolean(element.disabled || element.getAttribute('aria-disabled') === 'true'), x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2};
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
    // Navigation and asynchronously loaded connection fields can move a control.
    // Use stable post-layout coordinates before dispatching the physical click.
    for (let attempt = 0; attempt < 10; attempt++) {
      await delay(50);
      const next = await this.target(specification);
      const stable = Math.abs(next.x - target.x) < 1 && Math.abs(next.y - target.y) < 1;
      target = next;
      if (stable) break;
      assert.ok(attempt < 9, 'Control position stabilized');
    }
    const { x, y } = target;
    await this.call('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
    await this.call('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
    await this.call('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
  }
  async fill(name, text) {
    await this.click({ tag: 'input,textarea', name });
    await this.call('Input.dispatchKeyEvent', { type: 'keyDown', key: 'a', code: 'KeyA', modifiers: 2, windowsVirtualKeyCode: 65 });
    await this.call('Input.dispatchKeyEvent', { type: 'keyUp', key: 'a', code: 'KeyA', modifiers: 2, windowsVirtualKeyCode: 65 });
    await this.call('Input.insertText', { text });
  }
  async screenshot(name) {
    const { data } = await this.call('Page.captureScreenshot', { format: 'png' });
    await writeFile(resolve(`build/browser-${name}.png`), Buffer.from(data, 'base64'));
  }
}

export async function connect(profile) {
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

