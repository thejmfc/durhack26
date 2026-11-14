// Headless Chrome driver. usage: node tools/drive.mjs <url> [--shot out.png] [--wait-batch] [--eval "js"] [--settle ms]
import puppeteer from 'puppeteer-core';

const args = process.argv.slice(2), url = args[0];
const opt = (k) => { const i = args.indexOf(k); return i < 0 ? null : args[i + 1]; };
const browser = await puppeteer.launch({
  executablePath: process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  headless: true, protocolTimeout: 0,
  args: ['--use-angle=metal', '--enable-gpu', '--ignore-gpu-blocklist', '--window-size=1500,950'],
});
const page = await browser.newPage();
await page.setViewport({ width: 1500, height: 950 });
page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') console.log(`[${m.type()}]`, m.text().slice(0, 300)); });
page.on('pageerror', (e) => console.log('[pageerror]', e.message));
await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.waitForFunction('window.__skyline', { timeout: 120000 });
if (opt('--eval')) console.log(JSON.stringify(await page.evaluate(`(async () => { ${opt('--eval')} })()`)));
if (args.includes('--wait-batch')) {
  let last = -1;
  for (;;) {
    const b = await page.evaluate('window.__batch'), st = await page.evaluate('document.getElementById("status").textContent');
    if (b && b.i !== last && b.i % 10 === 0) { console.log(`${b.i}/${b.n}`); last = b.i; }
    if (b?.done) break;
    if (st && st !== 'Batch running...') { console.log('status:', st); process.exitCode = 1; break; }
    await new Promise((r) => setTimeout(r, 2000));
  }
}
await new Promise((r) => setTimeout(r, Number(opt('--settle') ?? 500)));
if (opt('--shot')) await page.screenshot({ path: opt('--shot') });
await browser.close();
