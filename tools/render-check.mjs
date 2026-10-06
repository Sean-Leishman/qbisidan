// Usage: node tools/render-check.mjs <file://...page.html> <out.png> [console-marker] [timeout-ms] <chrome-profile-dir>
// Found the places page bugs that 40 green unit tests could not: a map with no pins (Leaflet
// view set too late) and a base map that --screenshot mode never waited long enough to draw.
// Render a page in headless Chrome over the DevTools protocol, wait for a console marker
// (or a timeout), then screenshot. Unlike --screenshot, this waits in real time, so
// MapLibre's worker-drawn tiles get a chance to finish.
import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
const [url, out, waitFor = 'MAP IDLE', timeoutMs = '30000', profile] = process.argv.slice(2);
const port = 9333;
const chrome = spawn('google-chrome', ['--headless=new', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  `--remote-debugging-port=${port}`, '--window-size=1000,1300', '--hide-scrollbars', `--user-data-dir=${profile}`, 'about:blank'],
  { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));
let target;
for (let i = 0; i < 50 && !target; i++) {
  try { target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json(); }
  catch { await sleep(200); }
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener('open', r, { once: true }));
let id = 0; const pending = new Map(); const logs = [];
ws.addEventListener('message', ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result || m.error); pending.delete(m.id); }
  if (m.method === 'Runtime.consoleAPICalled') logs.push(m.params.args.map(a => a.value ?? a.description).join(' '));
  if (m.method === 'Runtime.exceptionThrown') logs.push('EXCEPTION ' + m.params.exceptionDetails.exception?.description?.split('\n')[0]);
});
const send = (method, params = {}) => new Promise(r => { const n = ++id; pending.set(n, r); ws.send(JSON.stringify({ id: n, method, params })); });
await send('Runtime.enable'); await send('Page.enable');
await send('Page.navigate', { url });
const start = Date.now();
while (Date.now() - start < +timeoutMs && !logs.some(l => l.includes(waitFor))) await sleep(250);
await sleep(1500);  // let the last tiles paint after idle
const shot = await send('Page.captureScreenshot', { format: 'png' });
writeFileSync(out, Buffer.from(shot.data, 'base64'));
console.log(JSON.stringify({ reached: logs.some(l => l.includes(waitFor)), seconds: ((Date.now() - start) / 1000).toFixed(1), logs }));
ws.close(); chrome.kill();
