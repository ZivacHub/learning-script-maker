// Plays Kartice and Quiz (the Igre tab) in a real browser. Run by tests/render/test_games.py (skipped when no browser is found).
//   node games_check.mjs <script.html>   -> prints one JSON document with what happened
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.SM_PLAYWRIGHT_CORE);

const browser = await chromium.launch({ executablePath: process.env.SM_CHROMIUM });
const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
const page = await context.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));
await page.goto(pathToFileURL(process.argv[2]).href);

const out = { errors };
out.tabs = await page.evaluate(() => window.__SM_MODES);
await page.evaluate(() => SM.mode.set('igre'));
out.visible = await page.evaluate(() => getComputedStyle(document.getElementById('igre')).display);
out.gradivoHidden = await page.evaluate(() => getComputedStyle(document.getElementById('gradivo')).display === 'none');
out.verbatimQuestions = await page.evaluate(() => document.querySelectorAll('#gradivo article.qa[data-st="s"]').length);

// ---- Kartice: Ne znam -> Znam -> Znam; everything else "Siguran/na sam"
await page.click('.g-game[data-game="kartice"]');
const firstQ = await page.textContent('.g-q');
await page.click('#g-flip');
out.firstButtons = await page.$$eval('#g-actions button', (b) => b.map((x) => x.textContent));
await page.click('[data-rate="ne"]');
out.cards = { again: [], buttonsOnRepeat: null, usedOnlyVerbatim: true };
for (let i = 0; i < 60; i++) {
  if (await page.$('.g-summary')) break;
  const q = await page.textContent('.g-q');
  const again = (await page.$('.g-again')) ? await page.textContent('.g-again') : '';
  await page.click('#g-flip');
  if (q === firstQ) {
    out.cards.again.push(again);
    out.cards.buttonsOnRepeat = await page.$$eval('#g-actions button', (b) => b.map((x) => x.textContent));
    await page.click('[data-rate="znam"]');
  } else {
    await page.click('[data-rate="siguran"]');
  }
}
out.cards.summary = (await page.textContent('.g-summary')).replace(/\s+/g, ' ').slice(0, 80);
out.cards.missedListed = (await page.$$('.g-missed li')).length;
out.mastered = await page.evaluate(() => Object.keys(SM.getJSON('games', {}).mastered || {}).length);

// ---- Quiz: first answer wrong, then right every time; the keyboard picks an option with a number key
await page.click('#g-back-to');
await page.click('.g-game[data-game="quiz"]');
out.quiz = { again: [], optionCounts: [], keyboardWorks: false, onlyVerbatimOptions: true };
const quizFirst = await page.textContent('.g-q');
const rightOf = () => page.getAttribute('.g-card', 'data-id');
let firstDone = false;
for (let i = 0; i < 80; i++) {
  if (await page.$('.g-summary')) break;
  const q = await page.textContent('.g-q');
  out.quiz.optionCounts.push(await page.$$eval('.g-opt', (o) => new Set(o.map((x) => x.dataset.id)).size));
  out.quiz.onlyVerbatimOptions = out.quiz.onlyVerbatimOptions && await page.evaluate(() =>
    [...document.querySelectorAll('.g-opt')].every((o) => document.getElementById(o.dataset.id).getAttribute('data-st') === 's'));
  const again = (await page.$('.g-again')) ? await page.textContent('.g-again') : '';
  if (q === quizFirst) out.quiz.again.push(again);
  const rightId = await rightOf();
  if (q === quizFirst && !firstDone) {
    firstDone = true;
    const wrongId = await page.evaluate((r) => [...document.querySelectorAll('.g-opt')].find((o) => o.dataset.id !== r).dataset.id, rightId);
    await page.click(`.g-opt[data-id="${wrongId}"]`);
    out.quiz.feedbackWrong = await page.textContent('#g-feedback');
    out.quiz.fullAnswerShown = await page.evaluate(() => !document.getElementById('g-back').hidden);
  } else if (!out.quiz.keyboardWorks) {
    const index = await page.evaluate((r) => [...document.querySelectorAll('.g-opt')].findIndex((o) => o.dataset.id === r), rightId);
    await page.keyboard.press(String(index + 1));
    out.quiz.keyboardWorks = (await page.textContent('#g-feedback')).startsWith('Točno');
  } else {
    await page.click(`.g-opt[data-id="${rightId}"]`);
  }
  await page.click('#g-next');
}
out.quiz.summary = (await page.textContent('.g-summary')).replace(/\s+/g, ' ').slice(0, 80);
out.quiz.missedListed = (await page.$$('.g-missed li')).length;
out.quiz.questionsAsked = out.quiz.optionCounts.length;

// ---- what you mastered is remembered after a reload
await page.reload();
await page.evaluate(() => SM.mode.set('igre'));
out.afterReload = (await page.textContent('.g-mastered')).replace(/\s+/g, ' ').trim();
await browser.close();
console.log(JSON.stringify(out));
