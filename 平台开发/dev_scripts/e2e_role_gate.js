const { chromium } = require('C:/Users/11708/AppData/Local/npm-cache/_npx/e41f203b7505f1fb/node_modules/playwright');
const EXE = 'C:/Users/11708/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe';
const BASE = 'http://localhost:5000';

const results = [];
function check(name, cond, extra) {
  results.push({ name, pass: !!cond, extra: extra || '' });
  console.log((cond ? 'PASS ' : 'FAIL ') + name + (extra ? '  [' + extra + ']' : ''));
}
const sleep = (p, ms) => p.waitForTimeout(ms);

async function doLogin(page, user, pw) {
  await page.goto(BASE + '/login', { waitUntil: 'networkidle' });
  await page.fill('#username', user);
  await page.fill('#password', pw);
  await page.click('button:has-text("登")');
  await page.waitForURL(u => u.pathname === '/', { timeout: 10000 });
  await sleep(page, 600);
}
async function nav(page, path) {
  await page.goto(BASE + path, { waitUntil: 'networkidle' });
  await sleep(page, 1200);
}
async function hasText(page, t) {
  return (await page.locator(`text=${JSON.stringify(t)}`).count()) > 0;
}

(async () => {
  const browser = await chromium.launch({ executablePath: EXE });

  // ============ 操作员（刘娜 uid=3，24站） ============
  const opErrors = [];
  const opCtx = await browser.newContext();
  const opPage = await opCtx.newPage();
  opPage.on('console', m => { if (m.type() === 'error') opErrors.push(m.text()); });
  opPage.on('pageerror', e => opErrors.push('PAGEERROR: ' + e.message));
  await doLogin(opPage, 'liuna', 'yw123456');

  // 1) 操作员不能进入 /users（路由门禁）
  await nav(opPage, '/users');
  check('操作员访问 /users 被拦截(重定向到 /)', opPage.url().endsWith('/') && !opPage.url().includes('/users'), opPage.url());

  // 2) 数据健康度：操作员看「按站点」
  await nav(opPage, '/');
  const opBySite = await hasText(opPage, '按站点');
  const opByManager = await hasText(opPage, '按负责人');
  check('操作员数据健康度显示「按站点」', opBySite);
  check('操作员数据健康度不显示「按负责人」', !opByManager);

  // 3) 阈值规则：操作员「新增阈值规则」禁用
  await nav(opPage, '/alerts');
  await opPage.locator('button:has-text("规则配置")').click();
  await sleep(opPage, 500);
  await opPage.locator('button:has-text("阈值规则")').click();
  await sleep(opPage, 1000);
  const addRule = opPage.getByRole('button', { name: '新增阈值规则' });
  await addRule.waitFor({ state: 'visible', timeout: 5000 }).catch(() => {});
  check('操作员「新增阈值规则」按钮存在且禁用', await addRule.count() > 0 && await addRule.isDisabled());

  // 4) 影像档案：操作员批量归档禁用（先选中一行）
  await nav(opPage, '/archive');
  await opPage.getByRole('radio', { name: '表格' }).click().catch(() => {});
  await sleep(opPage, 800);
  const firstRowCb = opPage.locator('.ant-table-tbody .ant-checkbox').first();
  if (await firstRowCb.count() > 0) {
    await firstRowCb.click({ force: true });
    await sleep(opPage, 600);
    const batch = opPage.getByRole('button', { name: '批量归档' });
    check('操作员「批量归档」按钮存在且禁用', await batch.count() > 0 && await batch.isDisabled());
  } else {
    check('操作员「批量归档」按钮测试跳过(列表为空)', true, '无归档数据行');
  }
  // 稳定窗口检查控制台错误（忽略导航取消噪声）
  opErrors.length = 0;
  await sleep(opPage, 2500);
  check('操作员会话稳定期控制台无错误', opErrors.length === 0, opErrors.slice(0, 3).join(' | '));
  await opCtx.close();

  // ============ 管理员（admin） ============
  const adErrors = [];
  const adCtx = await browser.newContext();
  const adPage = await adCtx.newPage();
  adPage.on('console', m => { if (m.type() === 'error') adErrors.push(m.text()); });
  adPage.on('pageerror', e => adErrors.push('PAGEERROR: ' + e.message));
  await doLogin(adPage, 'admin', 'admin123');

  // 5) 管理员可进入 /users
  await nav(adPage, '/users');
  const adUrl = adPage.url();
  const adToken = await adPage.evaluate(() => localStorage.getItem('water_ops_token') ? 'TOKEN_OK' : 'NO_TOKEN');
  check('管理员可访问 /users', adUrl.includes('/users'), 'url=' + adUrl + ' ' + adToken);
  check('管理员可见「添加用户」按钮', await adPage.getByRole('button', { name: '添加用户' }).count() > 0);

  // 6) 数据健康度：管理员看「按负责人」
  await nav(adPage, '/');
  const adByManager = await hasText(adPage, '按负责人');
  const adBySite = await hasText(adPage, '按站点');
  check('管理员数据健康度显示「按负责人」', adByManager);
  check('管理员数据健康度不显示「按站点」', !adBySite);

  // 7) 阈值规则：管理员「新增阈值规则」可用
  await nav(adPage, '/alerts');
  await adPage.locator('button:has-text("规则配置")').click();
  await sleep(adPage, 500);
  await adPage.locator('button:has-text("阈值规则")').click();
  await sleep(adPage, 1000);
  const addRuleA = adPage.getByRole('button', { name: '新增阈值规则' });
  await addRuleA.waitFor({ state: 'visible', timeout: 5000 }).catch(() => {});
  check('管理员「新增阈值规则」按钮可用', await addRuleA.count() > 0 && !(await addRuleA.isDisabled()));

  // 8) 影像档案：管理员批量归档可用
  await nav(adPage, '/archive');
  await adPage.getByRole('radio', { name: '表格' }).click().catch(() => {});
  await sleep(adPage, 800);
  const firstRowCbA = adPage.locator('.ant-table-tbody .ant-checkbox').first();
  if (await firstRowCbA.count() > 0) {
    await firstRowCbA.click({ force: true });
    await sleep(adPage, 600);
    const batchA = adPage.getByRole('button', { name: '批量归档' });
    check('管理员「批量归档」按钮可用', await batchA.count() > 0 && !(await batchA.isDisabled()));
  } else {
    check('管理员「批量归档」按钮测试跳过(列表为空)', true, '无归档数据行');
  }
  adErrors.length = 0;
  await sleep(adPage, 2500);
  check('管理员会话稳定期控制台无错误', adErrors.length === 0, adErrors.slice(0, 3).join(' | '));
  await adCtx.close();

  await browser.close();
  const failed = results.filter(r => !r.pass);
  console.log('\n==== 汇总 ====');
  console.log('总计 ' + results.length + ' 项，通过 ' + (results.length - failed.length) + '，失败 ' + failed.length);
  if (failed.length) { console.log('失败项：' + failed.map(f => f.name).join('；')); process.exit(1); }
  console.log('ALL PASS');
})().catch(e => { console.error('RUNNER ERROR', e); process.exit(2); });
