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
  await p.waitForTimeout(1500);
  const items = await p.locator('.ant-menu-item').all();
  console.log('menu count=', items.length);
  for (const it of items) {
    const t = (await it.innerText()).replace(/\s+/g, ' ').trim();
    console.log('  |', t, '|');
  }
  // 也试试用 getByRole menuitem
  const roles = await p.getByRole('menuitem').all();
  console.log('menuitem role count=', roles.length);
  await browser.close();
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
