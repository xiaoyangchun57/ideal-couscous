const { chromium } = require('C:/Users/11708/AppData/Local/npm-cache/_npx/e41f203b7505f1fb/node_modules/playwright');
const fs = require('fs');

const BASE = 'http://localhost:5000';
const log = (...a) => console.log('[E2E]', ...a);

(async () => {
  const aid = fs.readFileSync('e2e_photo_aid.txt', 'utf8').trim();
  const browser = await chromium.launch({
    executablePath: 'C:/Users/11708/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe',
  });
  const p = await browser.newPage();
  const errors = [];
  p.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  p.on('pageerror', e => errors.push('PAGEERR ' + e.message));

  // 登录
  await p.goto(BASE + '/login', { waitUntil: 'networkidle' });
  await p.waitForSelector('input#username', { timeout: 10000 });
  await p.fill('input#username', 'admin');
  await p.fill('input#password', 'admin123');
  await p.getByRole('button', { name: /登\s*录/ }).click();
  await p.waitForURL('**/', { timeout: 10000 });
  log('已登录');

  // 进入待办审核（直接走路由，菜单可能按角色折叠）
  await p.goto(BASE + '/audit', { waitUntil: 'networkidle' });
  await p.waitForTimeout(1500);
  log('已进入待办审核');

  // 找到含该照片标题的行，点其“审核”按钮
  const row = p.locator('tr', { hasText: '高锰酸盐指数仪器质控照片' }).first();
  await row.waitFor({ state: 'visible', timeout: 8000 });
  await row.getByRole('button', { name: '审核' }).click();
  await p.waitForTimeout(1200);

  // 弹窗应出现
  const modalTitle = p.locator('.ant-modal-title', { hasText: '审核' });
  const modalCount = await modalTitle.count();
  log('审核弹窗可见 =', modalCount > 0);
  if (modalCount === 0) throw new Error('审核弹窗未出现');

  // 弹窗内应展示“自动归类”与水印说明
  const autoCat = await p.locator('.ant-modal', { hasText: '自动归类' }).count();
  const wm = await p.locator('.ant-modal', { hasText: '水印说明' }).count();
  log('弹窗含自动归类=', autoCat > 0, '水印说明=', wm > 0);

  // 点击“审核通过”
  const okBtn = p.locator('.ant-modal-footer button.ant-btn-primary').filter({ visible: true }).first();
  await okBtn.click();
  await p.waitForTimeout(1500);
  log('已点审核通过');

  await browser.close();

  // DB 断言
  const { execSync } = require('child_process');
  const out = execSync(
    `"C:/Users/11708/.workbuddy/binaries/python/versions/3.13.12/python.exe" -c "import sqlite3;c=sqlite3.connect('backend/data/water.db');c.row_factory=sqlite3.Row;r=c.execute('SELECT review_status,recognized_category,review_required,requirement_id FROM operation_attachments WHERE id=${aid}').fetchone();print(dict(r))"`,
    { encoding: 'utf8' }
  );
  const row_db = JSON.parse(out.replace(/'/g, '"').replace(/False/g, 'false').replace(/True/g, 'true').replace(/None/g, 'null'));
  log('DB review_status =', row_db.review_status);
  if (row_db.review_status !== 'approved') throw new Error('审核未落库 approved');
  if (errors.length) { console.log('CONSOLE_ERRORS:', errors.slice(0, 5)); }
  console.log('✅ 照片审核 UI 闭环 E2E 通过');
})().catch(e => { console.error('❌ E2E 失败:', e.message); process.exit(1); });
