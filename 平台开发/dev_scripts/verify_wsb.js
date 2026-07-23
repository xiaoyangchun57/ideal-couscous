const { chromium } = require('playwright');
const PNG = 'E:/杂七杂八/水质运维/平台开发/test_reagent.png';

const CHROME = 'C:/Users/11708/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe';
const BASE = 'http://127.0.0.1:5000';

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME });
  const page = await browser.newPage();
  const logs = [];
  page.on('console', m => { if (m.type() === 'error') logs.push(`[err] ${m.text()}`); });
  page.on('pageerror', e => logs.push(`[pageerror] ${e.message}`));

  const out = {};
  try {
    await page.goto(`${BASE}/archive`, { waitUntil: 'networkidle', timeout: 30000 });
    if (await page.$('#username')) {
      await page.fill('#username', 'admin');
      await page.fill('#password', 'admin123');
      await page.click('button:has-text("登")');
      await page.waitForTimeout(2500);
      await page.goto(`${BASE}/archive`, { waitUntil: 'networkidle', timeout: 30000 });
    }
    await page.waitForSelector('.ant-card', { timeout: 15000 });

    // Radio 诊断
    out.radioCount = await page.evaluate(() => document.querySelectorAll('.ant-radio-button').length);
    out.radioTexts = await page.evaluate(() =>
      [...document.querySelectorAll('.ant-radio-button')].map(e => e.innerText.replace(/\s+/g, '')));
    out.bodyHasGrid = await page.evaluate(() => document.body.innerText.includes('网格'));
    out.bodyHasTable = await page.evaluate(() => document.body.innerText.includes('表格'));

    // 打开上传弹窗
    await page.locator('button:has-text("上传资料")').first().click();
    await page.waitForTimeout(800);
    out.modalSelects = await page.evaluate(() =>
      [...document.querySelectorAll('.ant-modal:visible .ant-select')].map(s => s.getAttribute('placeholder') || '(none)'));
    out.modalSelectCount = out.modalSelects.length;

    // 打开第一个下拉（来源类型）
    const firstSel = page.locator('.ant-modal:visible .ant-select').first();
    await firstSel.click();
    await page.waitForTimeout(500);
    out.dropdownOptions = await page.evaluate(() =>
      [...document.querySelectorAll('.ant-select-item-option')].map(o => o.innerText.replace(/\s+/g, '')));

    // 选“试剂配置照片”
    const opt = page.locator('.ant-select-item-option:has-text("试剂配置照片")').first();
    out.optFound = await opt.count();
    if (out.optFound) {
      await opt.click();
      await page.waitForTimeout(400);
      out.selectedSource = await page.evaluate(() => {
        const s = document.querySelector('.ant-modal:visible .ant-select');
        return s ? (s.getAttribute('title') || s.innerText.replace(/\s+/g, '')) : '(none)';
      });
    }

    // 上传文件
    const fileInput = page.locator('.ant-modal:visible input[type="file"]').first();
    out.fileInputFound = await fileInput.count();
    if (out.fileInputFound) {
      await fileInput.setInputFiles(PNG);
      await page.waitForFunction(() => document.body.innerText.includes('上传成功'), { timeout: 15000 }).catch(() => {});
    }
    out.uploadSuccess = await page.evaluate(() => document.body.innerText.includes('上传成功'));

    // 关闭弹窗
    const close = page.locator('.ant-modal:visible .ant-modal-close');
    if (await close.count()) await close.first().click();
    await page.waitForTimeout(600);

    // 按来源=试剂配置照片 筛选（找筛选区来源下拉）
    await page.evaluate(() => {
      const sels = [...document.querySelectorAll('.ant-select')];
      const t = sels.find(s => (s.getAttribute('placeholder') || '').includes('来源类型'));
      if (t) t.click();
    });
    await page.waitForTimeout(500);
    const fopt = page.locator('.ant-select-item-option:has-text("试剂配置照片")').first();
    out.filterOptFound = await fopt.count();
    if (out.filterOptFound) { await fopt.click(); await page.waitForTimeout(1200); }
    out.listShowsReagent = await page.evaluate(() =>
      document.body.innerText.includes('test_reagent'));
  } catch (e) {
    out.ERROR = e.message;
  } finally {
    out.logs = logs.slice(0, 8);
    console.log(JSON.stringify(out, null, 2));
    await browser.close();
  }
})();
