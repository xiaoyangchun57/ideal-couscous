const { chromium } = require('C:/Users/11708/AppData/Local/npm-cache/_npx/e41f203b7505f1fb/node_modules/playwright');
const BASE = 'http://localhost:5000';
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
  await p.locator('input[placeholder="搜索文件名或描述..."]').fill('E2E归档测试唯一标识');
  await p.getByRole('button', { name: '搜索' }).click();
  await p.waitForTimeout(1200);
  const card = p.locator('.ant-card', { hasText: 'E2E归档测试唯一标识' }).first();
  await card.waitFor({ state: 'visible', timeout: 8000 });
  await card.locator('img').first().click();
  await p.waitForTimeout(1200);
  // dump all modal footers
  const footers = await p.locator('.ant-modal-footer').all();
  console.log('modal footers count =', footers.length);
  for (let i = 0; i < footers.length; i++) {
    const btns = await footers[i].locator('button').all();
    const txts = [];
    for (const b of btns) txts.push((await b.innerText()).replace(/\s+/g,' ').trim());
    console.log(`  footer[${i}] buttons:`, JSON.stringify(txts));
  }
  await browser.close();
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
