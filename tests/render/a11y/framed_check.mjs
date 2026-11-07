// The script inside the app's preview frame (step 5.8, D40). Run by tests/render/test_progress_framed.py.
//   node framed_check.mjs <parent.html> <script.html>
// parent.html stands in for the app page: it embeds the script in a locked frame (sandbox="allow-scripts", no storage,
// no address of its own), answers "hello" with a state, and keeps every message it gets in window.__log.
// Prints one JSON document: what the parent received and what the script showed.
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.SM_PLAYWRIGHT_CORE);

const [parentFile, scriptFile] = process.argv.slice(2);
const browser = await chromium.launch({ executablePath: process.env.SM_CHROMIUM });
const out = { errors: [], framed: {}, alone: {} };

async function shown(frame, index) {
  return frame.evaluate((i) => {
    const art = document.querySelectorAll('article[data-item]')[i];
    const bar = art.querySelector('.prog');
    return {
      id: art.getAttribute('data-item'),
      text: bar.querySelector('.prog-text').textContent,
      max: bar.getAttribute('aria-valuemax'),
      state: bar.getAttribute('data-state'),
      goal: art.querySelector('.prog-target').value,
    };
  }, index);
}

try {
  // ---- in the frame ----
  const page = await (await browser.newContext({ viewport: { width: 1000, height: 900 } })).newPage();
  page.on('pageerror', (e) => out.errors.push(`framed: ${e.message}`));
  await page.goto(pathToFileURL(parentFile).href);
  await page.waitForFunction(() => window.__log && window.__log.some((m) => m.kind === 'hello'), null, { timeout: 8000 });
  await page.waitForTimeout(300);
  const frame = page.frames().find((f) => f !== page.mainFrame());
  out.framed.opaque = await frame.evaluate(() => { try { return typeof localStorage === 'undefined' ? 'no-storage' : (localStorage.length, 'storage'); } catch (e) { return 'blocked'; } });
  out.framed.first = await shown(frame, 0);                 // the state says: 2 passes done, goal 3
  out.framed.second = await shown(frame, 1);                // the state says nothing about it: follows the app's goal (3)
  await page.evaluate(() => { window.__log.length = 0; });
  await frame.locator('article[data-item] .prog-learn').nth(1).click();
  await page.waitForFunction(() => window.__log.some((m) => m.kind === 'change'), null, { timeout: 4000 });
  out.framed.afterLearn = await shown(frame, 1);
  out.framed.changeLearn = await page.evaluate(() => window.__log.filter((m) => m.kind === 'change'));
  await page.evaluate(() => { window.__log.length = 0; });
  await frame.locator('article[data-item] .prog-target').nth(1).selectOption('5');
  await page.waitForFunction(() => window.__log.some((m) => m.kind === 'change'), null, { timeout: 4000 });
  out.framed.afterGoal = await shown(frame, 1);
  out.framed.changeGoal = await page.evaluate(() => window.__log.filter((m) => m.kind === 'change'));
  await page.evaluate(() => { window.__log.length = 0; });
  await frame.locator('article[data-item] .prog-undo').nth(1).click();
  await page.waitForFunction(() => window.__log.some((m) => m.kind === 'change'), null, { timeout: 4000 });
  out.framed.changeUndo = await page.evaluate(() => window.__log.filter((m) => m.kind === 'change'));
  // a second state from the app (newer for item 0, older for item 1): the newer change wins per item
  await page.evaluate(() => { document.querySelector('iframe').contentWindow.postMessage(window.__second, '*'); });
  await page.waitForTimeout(300);
  out.framed.afterSecond = [await shown(frame, 0), await shown(frame, 1)];
  await page.context().close();

  // ---- alone: no page around it, nothing is sent anywhere, everything else works ----
  const alone = await (await browser.newContext({ viewport: { width: 1000, height: 900 } })).newPage();
  alone.on('pageerror', (e) => out.errors.push(`alone: ${e.message}`));
  await alone.goto(pathToFileURL(scriptFile).href);
  await alone.locator('article[data-item] .prog-learn').first().click();
  out.alone.first = await shown(alone.mainFrame(), 0);
  out.alone.hasParent = await alone.evaluate(() => window.parent === window);
} finally {
  await browser.close();
}
console.log(JSON.stringify(out));
