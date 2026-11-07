// Accessibility checks that need a real browser. Run by tests/render/test_accessibility.py (skipped when no browser is found).
//   node browser_check.mjs axe      <pages.json>   -> axe-core on every page, size, colour scheme and tab state
//   node browser_check.mjs keyboard <pages.json>   -> keyboard-only flows
//   node browser_check.mjs reflow   <pages.json>   -> no sideways page scroll at 320 px (WCAG 1.4.10), every tab state
//   node browser_check.mjs progress <pages.json> <outdir> -> the progress bar flow (press, undo, goals, reload, save, load)
// pages.json: [{ "name": "exams", "file": "C:/.../exams.html" }, ...]. Prints one JSON document on stdout.
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { pathToFileURL, fileURLToPath } from 'node:url';
import path from 'node:path';

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = require(process.env.SM_PLAYWRIGHT_CORE);
const axeSource = readFileSync(path.join(here, '..', '..', 'vendor', 'axe', 'axe.min.js'), 'utf8');

const mode = process.argv[2];
const pages = JSON.parse(readFileSync(process.argv[3], 'utf8'));
const SIZES = [{ name: '375', width: 375, height: 812 }, { name: '1280', width: 1280, height: 900 }];
const SCHEMES = ['light', 'dark'];

const browser = await chromium.launch({ executablePath: process.env.SM_CHROMIUM });
const out = { axe: [], keyboard: [], reflow: [], progress: [], errors: [] };

async function open(spec, size, scheme, reducedMotion = 'no-preference') {
  const context = await browser.newContext({ viewport: { width: size.width, height: size.height }, colorScheme: scheme, reducedMotion });
  const page = await context.newPage();
  page.on('pageerror', (e) => out.errors.push(`${spec.name}: ${e.message}`));
  page.on('console', (m) => { if (m.type() === 'error') out.errors.push(`${spec.name}: ${m.text()}`); });
  page.on('request', (r) => { if (!r.url().startsWith('file:') && !r.url().startsWith('data:')) out.errors.push(`${spec.name}: request ${r.url()}`); });
  await page.goto(pathToFileURL(spec.file).href);
  await page.evaluate(() => { if (window.SM && SM.typeset) SM.typeset(); });   // formulas are typeset lazily: do all of them
  // progress bars in all three states (gray, yellow, green): axe measures the colours of each
  await page.evaluate(() => {
    const learn = [...document.querySelectorAll('article[data-item] .prog-learn')];
    if (learn.length > 1) {
      const goal = +learn[0].closest('article').querySelector('.prog').getAttribute('aria-valuemax');
      for (let i = 0; i < goal; i++) learn[0].click();
      learn[1].click();
    }
  });
  return { context, page };
}

// ---- axe ------------------------------------------------------------------------------------------------------------
async function runAxe(page) {
  await page.addScriptTag({ content: axeSource });
  return page.evaluate(async () => {
    const result = await axe.run(document, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] },
      resultTypes: ['violations'],
    });
    return result.violations.map((v) => ({
      id: v.id, impact: v.impact, help: v.help, count: v.nodes.length,
      nodes: v.nodes.slice(0, 3).map((n) => ({ target: n.target.join(' '), html: n.html.slice(0, 160), why: (n.any[0] || n.all[0] || n.none[0] || {}).message })),
    }));
  });
}

// The lesson menu on the left (D61) keeps the tabs and lesson buttons out of reach until it is open (the strip on top has no button).
async function menuOpen(page) {
  const btn = await page.$('#menu-btn');
  if (!btn || !(await btn.isVisible())) return false;
  if ((await btn.getAttribute('aria-expanded')) !== 'true') await btn.click();
  await page.waitForTimeout(300);
  return true;
}
async function menuClose(page, wasOpened) {
  if (!wasOpened) return;
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);
}

async function states(page) {
  // every tab the page has, plus Favoriti with two cards open, plus hint and fold-out boxes opened
  const tabs = await page.evaluate(() => [...document.querySelectorAll('.mode-sw button')].map((b) => b.dataset.mode));
  // the scroll-region scan runs on the next animation frame after a typeset: give it (and colour fades) time to finish
  if (!tabs.length) return [{ name: 'page', prepare: async () => { await page.waitForTimeout(450); } }];
  const hasMenu = await page.evaluate(() => !!document.getElementById('menu-btn') && getComputedStyle(document.getElementById('menu-btn')).display !== 'none');
  const open = hasMenu ? [{
    name: 'menu',
    prepare: async () => {
      await menuOpen(page);
      await page.waitForTimeout(450);
    },
  }] : [];
  return [...open, ...tabs.map((tab) => ({
    name: tab,
    prepare: async () => {
      if (tab === 'favoriti') {
        await page.evaluate(() => { document.querySelectorAll('article.qa .heart').forEach((h, i) => { if (i < 3) h.click(); }); });
      }
      const opened = await menuOpen(page);
      await page.click(`.mode-sw [data-mode="${tab}"]`);
      await menuClose(page, opened);
      await page.evaluate(() => {
        document.querySelectorAll('.fav-toggle').forEach((b, i) => { if (i < 2) b.click(); });
        document.querySelectorAll('details').forEach((d) => { d.open = true; });
        SM.typeset();
      });
      await page.waitForTimeout(450);   // let colour fades finish: axe must not measure a colour half way between two states
    },
  }))];
}

if (mode === 'axe') {
  for (const spec of pages) {
    for (const size of SIZES) {
      for (const scheme of SCHEMES) {
        const { context, page } = await open(spec, size, scheme);
        for (const state of await states(page)) {
          await state.prepare();
          out.axe.push({ page: spec.name, size: size.name, scheme, state: state.name, violations: await runAxe(page) });
        }
        await context.close();
      }
    }
  }
}

// ---- keyboard -----------------------------------------------------------------------------------------------------------
async function describeFocus(page) {
  return page.evaluate(() => {
    const el = document.activeElement;
    if (!el || el === document.body) return null;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    const cx = Math.min(Math.max(r.left + r.width / 2, 0), innerWidth - 1);
    const cy = Math.min(Math.max(r.top + r.height / 2, 0), innerHeight - 1);
    const top = document.elementFromPoint(cx, cy);
    const ring = (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0) || (cs.boxShadow && cs.boxShadow !== 'none');
    const name = (el.getAttribute('aria-label') || el.textContent || el.getAttribute('title') || el.getAttribute('placeholder') || '').replace(/\s+/g, ' ').trim();
    return {
      tag: el.tagName.toLowerCase(), id: el.id, cls: String(el.className).slice(0, 40), name: name.slice(0, 50),
      ring: !!ring, inView: r.bottom > 0 && r.top < innerHeight && r.width > 0,
      covered: !(top === el || el.contains(top) || (top && top.contains(el))),
      coveredBy: top ? top.tagName.toLowerCase() + '.' + String(top.className).slice(0, 30) : null,
    };
  });
}

if (mode === 'keyboard') {
  for (const spec of pages) {
    for (const size of SIZES) {
      // reduced motion: no smooth scrolling, so "is it in view" can be measured right after a key press
      const { context, page } = await open(spec, size, 'light', 'reduce');
      const report = { page: spec.name, size: size.name, firstIsSkipLink: false, skipLinkVisible: false, afterSkipInsideMain: false, stops: [], problems: [], actions: {} };
      await page.keyboard.press('Tab');
      const first = await describeFocus(page);
      report.firstIsSkipLink = !!first && first.cls.includes('skip');
      report.skipLinkVisible = await page.evaluate(() => { const r = document.activeElement.getBoundingClientRect(); return r.top >= 0 && r.bottom > 0 && r.left >= 0; });
      await page.keyboard.press('Enter');
      await page.keyboard.press('Tab');
      report.afterSkipInsideMain = await page.evaluate(() => !!document.activeElement.closest('main'));
      // walk the tab order from the top of the page
      await page.evaluate(() => { window.scrollTo(0, 0); document.activeElement.blur(); });
      await page.goto(pathToFileURL(spec.file).href);
      for (let i = 0; i < 70; i++) {
        await page.keyboard.press('Tab');
        await page.waitForTimeout(40);
        const f = await describeFocus(page);
        if (!f) break;
        report.stops.push(f);
        if (!f.name) report.problems.push(`no name: ${f.tag}.${f.cls}`);
        if (!f.ring) report.problems.push(`no focus ring: ${f.tag}.${f.cls} "${f.name}"`);
        if (!f.inView) report.problems.push(`focus not in view: ${f.tag}.${f.cls} "${f.name}"`);
        if (f.covered) report.problems.push(`focus covered by ${f.coveredBy}: ${f.tag}.${f.cls} "${f.name}"`);
      }
      // operate controls with the keyboard only
      const menuBtn = await page.$('#menu-btn');
      if (menuBtn && (await menuBtn.isVisible())) {
        // the menu button opens the menu with the keyboard, focus moves into it, Esc closes it and gives the focus back
        await menuBtn.focus();
        await page.keyboard.press('Enter');
        await page.waitForTimeout(300);
        report.actions.menuOpensWithEnter = (await menuBtn.getAttribute('aria-expanded')) === 'true';
        report.actions.menuFocusInside = await page.evaluate(() => !!document.activeElement.closest('#menu'));
        await page.keyboard.press('Escape');
        await page.waitForTimeout(300);
        report.actions.menuClosesWithEsc = (await menuBtn.getAttribute('aria-expanded')) === 'false';
        report.actions.menuFocusBack = await page.evaluate(() => document.activeElement.id === 'menu-btn');
        await page.keyboard.press('Enter');
        await page.waitForTimeout(300);
      }
      const tabButton = await page.$('.mode-sw [data-mode="favoriti"]');
      if (tabButton) {
        await tabButton.focus();
        await page.keyboard.press('Enter');
        report.actions.enterOnTab = await page.evaluate(() => document.documentElement.dataset.mode);
        await (await page.$('.mode-sw [data-mode="gradivo"]')).focus();
        await page.keyboard.press('Space');
        report.actions.spaceOnTab = await page.evaluate(() => document.documentElement.dataset.mode);
        if (menuBtn && (await menuBtn.isVisible())) { await page.keyboard.press('Escape'); await page.waitForTimeout(300); }
        const heart = await page.$('article.qa .heart');
        await heart.focus();
        await page.keyboard.press('Space');
        report.actions.heartPressedAfterSpace = await heart.getAttribute('aria-pressed');
        await page.keyboard.press('Enter');
        report.actions.heartPressedAfterEnter = await heart.getAttribute('aria-pressed');
      }
      const summary = await page.$('details summary');
      if (summary) {
        await summary.focus();
        const before = await page.evaluate(() => document.activeElement.parentElement.open);
        await page.keyboard.press('Enter');
        report.actions.detailsToggles = before !== (await page.evaluate(() => document.activeElement.parentElement.open));
      }
      const chip = await page.$('#f-none');
      if (chip) {
        await chip.focus();
        await page.keyboard.press('Space');
        report.actions.chipPressed = await chip.getAttribute('aria-pressed');
      }
      report.scrollRegions = await page.evaluate(() =>
        [...document.querySelectorAll('[data-scroll="1"]')].map((e) => ({ role: e.getAttribute('role'), label: e.getAttribute('aria-label'), tabindex: e.getAttribute('tabindex'), cls: String(e.className).slice(0, 30) })));
      out.keyboard.push(report);
      await context.close();
    }
  }
}

// ---- reflow: at 320 px wide nothing may force the page to scroll sideways --------------------------------------------------
if (mode === 'reflow') {
  for (const spec of pages) {
    const { context, page } = await open(spec, { name: '320', width: 320, height: 640 }, 'light');
    for (const state of await states(page)) {
      await state.prepare();
      const wide = await page.evaluate(() => {
        const limit = document.documentElement.clientWidth;
        const offenders = [...document.querySelectorAll('body *')]
          .filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.right > limit + 1 && !e.closest('.pills, .nav-in, pre, .tex-display, .verbatim, .own-block, .katex-display'); })
          .slice(0, 5).map((e) => e.tagName.toLowerCase() + '.' + String(e.className).slice(0, 30));
        // boxes that hide what does not fit (the tab switch has rounded ends and overflow hidden): text must not be cut off
        const clipped = [...document.querySelectorAll('.mode-sw, .mode-sw button, .pill, .chip')]
          .filter((e) => e.getBoundingClientRect().width > 0 && e.scrollWidth > e.clientWidth + 1)
          .slice(0, 5).map((e) => e.tagName.toLowerCase() + '.' + String(e.className).slice(0, 30) + ' "' + e.textContent.trim().slice(0, 20) + '"');
        return { sideways: document.documentElement.scrollWidth > limit + 1, offenders, clipped };
      });
      out.reflow.push({ page: spec.name, state: state.name, ...wide });
    }
    await context.close();
  }
}

// ---- progress: the bar flow, in a fresh browser profile per page ---------------------------------------------------------------------
if (mode === 'progress') {
  const { writeFileSync, readFileSync: read } = await import('node:fs');
  const outDir = process.argv[4];
  const read1 = (page, i = 0) => page.evaluate((i) => {
    const a = document.querySelectorAll('article[data-item]')[i];
    const bar = a.querySelector('.prog');
    return {
      state: bar.dataset.state, now: bar.getAttribute('aria-valuenow'), max: bar.getAttribute('aria-valuemax'),
      valuetext: bar.getAttribute('aria-valuetext'), text: bar.querySelector('.prog-text').textContent,
      ticks: bar.querySelectorAll('.prog-ticks i').length, goal: a.querySelector('.prog-target').value,
      undoDisabled: a.querySelector('.prog-undo').getAttribute('aria-disabled'),
    };
  }, i);
  const roll = (page) => page.evaluate(() => {
    const t = document.querySelector('.study-total');
    const u = document.querySelector('.study-unit');
    return {
      total: t && { text: t.querySelector('.prog-text').textContent, note: t.querySelector('.study-note').textContent, state: t.querySelector('.prog').dataset.state },
      unit: u && { text: u.querySelector('.prog-text').textContent, note: u.querySelector('.study-note').textContent, goal: u.querySelector('select').value },
    };
  });
  for (const spec of pages) {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, reducedMotion: 'reduce', acceptDownloads: true });
    const page = await context.newPage();
    page.on('pageerror', (e) => out.errors.push(`${spec.name}: ${e.message}`));
    page.on('console', (m) => { if (m.type() === 'error') out.errors.push(`${spec.name}: ${m.text()}`); });
    page.on('request', (r) => { if (!r.url().startsWith('file:') && !r.url().startsWith('data:')) out.errors.push(`${spec.name}: request ${r.url()}`); });
    await page.goto(pathToFileURL(spec.file).href);
    const r = { page: spec.name, hasProgress: (await page.$$('.study')).length > 0 };
    if (r.hasProgress) {
      r.start = await read1(page);
      r.startRoll = await roll(page);
      const learn = (i) => page.locator('article[data-item]').nth(i).locator('.prog-learn');
      await learn(0).click();
      r.afterOne = await read1(page);
      for (let i = 1; i < +r.start.max; i++) await learn(0).click();
      r.afterGoal = await read1(page);
      r.rollAfterGoal = await roll(page);
      await page.reload();
      r.afterReload = await read1(page);
      // keyboard only: undo with Enter, learn again with Space
      await page.locator('article[data-item]').first().locator('.prog-undo').focus();
      await page.keyboard.press('Enter');
      r.afterUndoKey = await read1(page);
      await page.locator('article[data-item]').first().locator('.prog-learn').focus();
      await page.keyboard.press('Space');
      r.afterSpaceKey = await read1(page);
      // undo on an empty item does nothing and keeps the focus where it is
      await page.locator('article[data-item]').nth(1).locator('.prog-undo').focus();
      await page.keyboard.press('Enter');
      r.undoOnEmpty = { item: await read1(page, 1), focusKept: await page.evaluate(() => document.activeElement.classList.contains('prog-undo')) };
      // the goal of one question, then of a whole lesson
      await page.locator('article[data-item]').nth(1).locator('.prog-target').selectOption('4');
      r.ownGoal = await read1(page, 1);
      r.rollOwnGoal = await roll(page);
      await page.reload();
      r.ownGoalAfterReload = await read1(page, 1);
      await page.locator('.study-unit select').first().selectOption('3');
      r.lessonGoal = { first: await read1(page, 0), second: await read1(page, 1), roll: await roll(page) };
      // save to a file
      const [download] = await Promise.all([page.waitForEvent('download'), page.click('.prog-save')]);
      const saved = path.join(outDir, `${spec.name}-saved.json`);
      await download.saveAs(saved);
      r.savedName = download.suggestedFilename();
      r.savedFile = saved;
      // load: a file for another subject and a broken file are refused; a good file wins item by item when it is newer
      const data = JSON.parse(read(saved, 'utf8'));
      const later = { ...data.items[0], passes_done: 1, updated_at: '2999-01-01T00:00:00+00:00' };
      const unknown = { item_id: 'no-such-question', passes_done: 1, target_passes: 2, updated_at: '2999-01-01T00:00:00+00:00' };
      writeFileSync(path.join(outDir, `${spec.name}-wrong.json`), JSON.stringify({ ...data, predmet: 'Neki drugi predmet' }));
      writeFileSync(path.join(outDir, `${spec.name}-good.json`), JSON.stringify({ ...data, items: [later, unknown] }));
      writeFileSync(path.join(outDir, `${spec.name}-junk.json`), '{ not json');
      await page.setInputFiles('.prog-file', path.join(outDir, `${spec.name}-wrong.json`));
      await page.waitForTimeout(200);
      r.msgWrong = await page.locator('.study-msg').innerText();
      await page.setInputFiles('.prog-file', path.join(outDir, `${spec.name}-junk.json`));
      await page.waitForTimeout(200);
      r.msgJunk = await page.locator('.study-msg').innerText();
      r.afterRefused = await read1(page);
      await page.setInputFiles('.prog-file', path.join(outDir, `${spec.name}-good.json`));
      await page.waitForTimeout(200);
      r.msgGood = await page.locator('.study-msg').innerText();
      r.afterLoad = await read1(page);
      r.liveRegion = await page.evaluate(() => document.querySelector('body > p[role="status"]').textContent);
    }
    out.progress.push(r);
    await context.close();
  }
}

await browser.close();
process.stdout.write(JSON.stringify(out));
