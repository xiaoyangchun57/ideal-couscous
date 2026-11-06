const { chromium } = require('C:/Users/11708/AppData/Local/npm-cache/_npx/e41f203b7505f1fb/node_modules/playwright');
const BASE = 'http://localhost:5000';
const MARK = 'E2E归档测试唯一标识';
const clean = s => s.replace(/\s+/g, ' ').trim();
(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Users/11708/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe' });
  const p = await browser.newPage();
  await p.goto(BASE + '/login', { waitUntil: 'networkidle' });
  await p.waitForSelector('input#username');
  await p.fill('input#username', 'admin'); await p.fill('input#password', 'admin123');
  await p.getByRole('button', { name: /登\s*录/ }).click();
  await p.waitForURL('**/');
  await p.goto(BASE + '/archive', { waitUntil: 'networkidle' });
  await p.waitForTimeout(1500);
  await p.locator('input[placeholder="搜索文件名或描述..."]').fill(MARK);
  await p.getByRole('button', { name: '搜索' }).click();
  await p.waitForTimeout(1200);
  const card = p.locator('.ant-card', { hasText: MARK }).first();
  await card.locator('img').first().click();
  await p.waitForTimeout(1000);
  const dumpBtns = async (label) => {
    const modals = await p.locator('.ant-modal-content').all();
    console.log(`-- ${label}: modal count=${modals.length}`);
    for (let i = 0; i < modals.length; i++) {
      const m = modals[i];
      const title = clean(await m.locator('.ant-modal-title').first().innerText().catch(() => '(none)'));
      const bs = await m.getByRole('button').all();
      const names = [];
      for (const b of bs) names.push(clean(await b.innerText()));
      console.log(`   modal[${i}] title="${title}" buttons=${JSON.stringify(names)}`);
    }
  };
  await dumpBtns('归档前');
  await p.locator('.ant-modal-content', { hasText: MARK }).first().getByRole('button', { name: '归档' }).first().click();
  await p.waitForTimeout(900);
  await p.getByRole('button', { name: '确认归档' }).first().click();
  await p.waitForTimeout(1400);
  await dumpBtns('归档后');
  await browser.close();
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
