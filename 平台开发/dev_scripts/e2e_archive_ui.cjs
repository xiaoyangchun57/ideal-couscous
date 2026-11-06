const { chromium } = require('C:/Users/11708/AppData/Local/npm-cache/_npx/e41f203b7505f1fb/node_modules/playwright');
const fs = require('fs');
const { execSync } = require('child_process');
const BASE = 'http://localhost:5000';
const log = (...a) => console.log('[E2E]', ...a);
const PY = 'C:/Users/11708/.workbuddy/binaries/python/versions/3.13.12/python.exe';
const MARK = 'E2E归档测试唯一标识';

function dbArchived(aid) {
  const out = execSync(
    `"${PY}" -c "import sqlite3;c=sqlite3.connect('backend/data/water.db');r=c.execute('SELECT archived FROM operation_attachments WHERE id=${aid}').fetchone();print(r[0] if r else 'MISSING')"`,
    { encoding: 'utf8' });
  return out.trim();
}

(async () => {
  const aid = fs.readFileSync('e2e_archive_aid.txt', 'utf8').trim();
  const browser = await chromium.launch({ executablePath: 'C:/Users/11708/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe' });
  const p = await browser.newPage();
  const errors = [];
  p.on('pageerror', e => errors.push('PAGEERR ' + e.message));

  await p.goto(BASE + '/login', { waitUntil: 'networkidle' });
  await p.waitForSelector('input#username');
  await p.fill('input#username', 'admin'); await p.fill('input#password', 'admin123');
  await p.getByRole('button', { name: /登\s*录/ }).click();
  await p.waitForURL('**/');
  log('已登录');

  await p.goto(BASE + '/archive', { waitUntil: 'networkidle' });
  await p.waitForTimeout(1500);
  log('已进入影像档案');

  await p.locator('input[placeholder="搜索文件名或描述..."]').fill(MARK);
  await p.getByRole('button', { name: '搜索' }).click();
  await p.waitForTimeout(1200);

  const openPreview = async () => {
    const card = p.locator('.ant-card', { hasText: MARK }).first();
    await card.waitFor({ state: 'visible', timeout: 8000 });
    await card.locator('img').first().click();
    await p.waitForTimeout(1000);
  };

  // ---- 归档 ----
  await openPreview();
  const previewModal = p.locator('.ant-modal-content', { hasText: MARK }).first();
  await previewModal.getByRole('button', { name: '归档' }).first().click();
  const reasonModal = p.locator('.ant-modal-title', { hasText: '归档影像资料' });
  await reasonModal.first().waitFor({ state: 'visible', timeout: 8000 });
  log('归档原因弹窗可见 = true');
  await p.getByRole('button', { name: '确认归档' }).first().click();
  await p.waitForTimeout(1200);
  const a1 = dbArchived(aid);
  log('归档后 DB archived =', a1);
  if (a1 !== '1') throw new Error('归档未落库');

  // ---- 取消归档（预览弹窗仍在，已重渲染为取消归档）----
  await p.waitForTimeout(600);
  const previewModal2 = p.locator('.ant-modal-content', { hasText: MARK }).first();
  await previewModal2.getByRole('button', { name: '取消归档' }).first().click();
  const confirmCancel = p.getByRole('button', { name: '取消归档' });
  await confirmCancel.last().waitFor({ state: 'visible', timeout: 8000 });
  await confirmCancel.last().click();
  await p.waitForTimeout(1200);
  const a2 = dbArchived(aid);
  log('取消归档后 DB archived =', a2);
  if (a2 !== '0') throw new Error('取消归档未落库');

  await browser.close();
  if (errors.length) console.log('PAGE_ERRORS:', errors.slice(0, 3));
  console.log('✅ 影像归档 UI 闭环 E2E 通过');
})().catch(e => { console.error('❌ E2E 失败:', e.message); process.exit(1); });
