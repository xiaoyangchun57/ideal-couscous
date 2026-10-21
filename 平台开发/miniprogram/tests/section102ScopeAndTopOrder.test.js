/**
 * 10.2 行为级专项测试：顶部顺序、范围切换行为和触控区
 *
 * 真实 Page 实例化 + mock API 可控 Promise + 乱序验证
 * 覆盖：
 *  1. 计划页角色快捷区删除（结构级，保留）
 *  2. 同模式快速双击 → 旧响应不回退
 *  3. 跨模式切换 → 旧模式迟到响应不写共享状态
 *  4. 最新请求失败 → 清 pending、保留已提交范围
 *  5. 切回有缓存模式 → 范围匹配复用，不匹配重载
 *  6. 无 canViewAll → 不显示 + 零请求
 *  7. 触控热区 ≥ 88rpx（结构级，保留）
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const api = require('../services/api.js');

// ===== 全局 mock =====
global.getApp = () => ({ globalData: {} });
let pageDefinition;
global.Page = page => { pageDefinition = page; };
let cachedUser = { capabilities: { station_monitoring_public: false } };
global.wx = {
  navigateTo: () => {},
  pageScrollTo: () => {},
  hideTabBar: () => {},
  showTabBar: () => {},
  getStorageSync: key => key === 'user' ? cachedUser : null,
};
require('../pages/responsible-sites/responsible-sites.js');

const flush = () => new Promise(resolve => setImmediate(resolve));
const flushMicro = () => new Promise(resolve => setTimeout(resolve, 0));

// 创建可控 Promise 工厂
function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function makePage() {
  const page = Object.assign({}, pageDefinition, {
    data: JSON.parse(JSON.stringify(pageDefinition.data)),
  });
  page.setData = function(patch) { Object.assign(page.data, patch); };
  page.onLoad();
  return page;
}

// 备份原始 API
const orig = {
  responsibleSites: api.responsibleSites,
  stationMonitoringSites: api.stationMonitoringSites,
  reagentOverview: api.reagentOverview,
};

let tests = 0, passed = 0, failed = 0;
function test(name, fn) {
  tests++;
  try {
    const result = fn();
    if (result && typeof result.then === 'function') {
      // 不支持异步测试在顶层，同步方式用 flush
    }
    passed++;
    console.log('✔ ' + name);
  } catch (e) {
    failed++;
    console.log('✘ ' + name);
    console.log('  ' + e.message);
  }
}

// ===== 工具函数 =====
function mockSites(delays) {
  // 返回一个 sites API mock，每次调用返回一个可控 Promise
  // delays 是数组，每个元素 { scope, delay, response } 或直接 { scope, response }
  let callIndex = 0;
  const calls = [];
  api.responsibleSites = (options) => {
    calls.push(options);
    const d = deferred();
    const cfg = delays ? delays[callIndex] : null;
    callIndex++;
    if (cfg && cfg.response) {
      if (cfg.delay) setTimeout(() => d.resolve(cfg.response), cfg.delay);
      else d.resolve(cfg.response);
    } else {
      d.resolve({ scope: options.scope, available_scopes: ['mine', 'all'], scope_counts: { mine: 1, all: 2 }, items: [] });
    }
    return d.promise;
  };
  return { calls, deferreds: [] /* 简化：直接用 setTimeout 控制 */ };
}

function mockReagents(delays) {
  let callIndex = 0;
  const calls = [];
  api.reagentOverview = (options) => {
    calls.push(options);
    const d = deferred();
    const cfg = delays ? delays[callIndex] : null;
    callIndex++;
    if (cfg && cfg.response) {
      if (cfg.delay) setTimeout(() => d.resolve(cfg.response), cfg.delay);
      else d.resolve(cfg.response);
    } else {
      d.resolve({ scope: options.scope, available_scopes: ['mine', 'all'], scope_counts: { mine: 1, all: 2 }, items: [] });
    }
    return d.promise;
  };
  return { calls };
}

function makeSiteResponse(scope, items) {
  return { scope, available_scopes: ['mine', 'all'], scope_counts: { mine: 1, all: 2 }, items: items || [] };
}

function makeReagentResponse(scope, items) {
  return { scope, available_scopes: ['mine', 'all'], scope_counts: { mine: 1, all: 2 }, items: items || [] };
}

// ===== 同步测试：结构级 =====
const planWxml = fs.readFileSync(path.join(__dirname, '../pages/plan/plan.wxml'), 'utf8');
const planWxss = fs.readFileSync(path.join(__dirname, '../pages/plan/plan.wxss'), 'utf8');
const sitesWxml = fs.readFileSync(path.join(__dirname, '../pages/responsible-sites/responsible-sites.wxml'), 'utf8');
const sitesWxss = fs.readFileSync(path.join(__dirname, '../pages/responsible-sites/responsible-sites.wxss'), 'utf8');

test('计划页 WXML 不含 role-quick-section', () => {
  assert(!planWxml.includes('role-quick-section'));
});

test('计划页 WXML 不含 quick-entry-* 遗留', () => {
  assert(!planWxml.includes('quick-entry-card'));
});

test('计划页 WXSS 不含角色快捷区样式', () => {
  assert(!planWxss.includes('.role-quick-section'));
  assert(!planWxss.includes('.quick-entry-card'));
});

test('计划页 custom-navbar 后直接 segment-wrap', () => {
  const navClose = planWxml.indexOf('</custom-navbar>');
  const segStart = planWxml.indexOf('segment-wrap');
  const between = planWxml.slice(navClose, segStart);
  assert(!between.includes('role-quick'), '角色快捷区仍在中间');
  assert(!between.includes('quick-entry'), '快捷入口仍在中间');
});

test('.nav-scope 全局触控热区 ≥ 88rpx 且视觉 pill 固定 56rpx', () => {
  const appWxss = fs.readFileSync(path.join(__dirname, '../app.wxss'), 'utf8');
  const ruleMatch = appWxss.match(/\.nav-scope\s*\{([^}]+)\}/);
  assert(ruleMatch, '找不到全局 .nav-scope');
  const minHMatch = ruleMatch[1].match(/min-height\s*:\s*([\d.]+)rpx/);
  assert(minHMatch, '没有 min-height');
  assert(Number(minHMatch[1]) >= 88, 'min-height ' + minHMatch[1] + ' < 88');
  const pillMatch = appWxss.match(/\.nav-scope-pill\s*\{([^}]+)\}/);
  assert(pillMatch, '找不到 .nav-scope-pill');
  const hMatch = pillMatch[1].match(/height\s*:\s*([\d.]+)rpx/);
  assert(hMatch && Number(hMatch[1]) === 56, 'pill 高度未固定为 56rpx');
  assert(/background\s*:\s*var\(--color-primary\)/.test(pillMatch[1]), 'pill 底色未使用主题强调蓝');
  assert(/min-width\s*:\s*([\d.]+)rpx/.test(pillMatch[1]), 'pill 缺少定宽 min-width');
});

test('两页范围切换共用全局规范（页面级无残留私有样式）', () => {
  assert(!planWxss.includes('.scope-toggle'), 'plan.wxss 仍残留 .scope-toggle');
  assert(!sitesWxss.includes('.stab-nav-scope'), 'sites.wxss 仍残留 .stab-nav-scope');
  assert(planWxml.includes('slot="right"') && planWxml.includes('nav-scope-pill'), 'plan 页未接入全局 .nav-scope');
  assert(sitesWxml.includes('slot="right"') && sitesWxml.includes('nav-scope-pill'), '站点页未接入全局 .nav-scope');
  assert(planWxml.includes('showBorder="{{false}}"'), 'plan 页 showBorder 未统一为 false');
});

test('站点 WXML 范围标签消费 pendingScope', () => {
  assert(sitesWxml.includes('pendingScope || scope'));
});

test('站点 WXML 有 is-pending class', () => {
  assert(sitesWxml.includes('is-pending'));
});

// ===== 结构测试：Sheet 成功态完整互斥 =====
test('更换 Sheet：表单外壳与成功态用 wx:if/wx:else 互斥', () => {
  // 表单块必须有 wx:if="{{!replaceSheet.success}}"
  assert.match(sitesWxml, /<block wx:if="\{\{!replaceSheet\.success\}\}">/, '更换表单外壳无 wx:if 控制');
  // 成功态必须是 wx:else
  assert.match(sitesWxml, /<view class="sheet-success" wx:else>/, '更换成功态非 wx:else');
  // sheet-handle 在 block 内（在 wx:if 之后、第一个 </block> 之前出现）
  const blockStart = sitesWxml.indexOf('<block wx:if="{{!replaceSheet.success}}">');
  const blockEnd = sitesWxml.indexOf('</block>', blockStart);
  assert(blockStart > 0 && blockEnd > blockStart, '找不到更换表单 block');
  const handleInBlock = sitesWxml.slice(blockStart, blockEnd).includes('sheet-handle');
  const headerInBlock = sitesWxml.slice(blockStart, blockEnd).includes('sheet-header');
  const bodyInBlock = sitesWxml.slice(blockStart, blockEnd).includes('sheet-body');
  const footerInBlock = sitesWxml.slice(blockStart, blockEnd).includes('sheet-footer');
  assert(handleInBlock, '更换 sheet-handle 不在互斥块内');
  assert(headerInBlock, '更换 sheet-header 不在互斥块内');
  assert(bodyInBlock, '更换 sheet-body 不在互斥块内');
  assert(footerInBlock, '更换 sheet-footer 不在互斥块内');
  // 成功态不在 block 内（是 wx:else 兄弟）
  const successInBlock = sitesWxml.slice(blockStart, blockEnd).includes('sheet-success');
  assert(!successInBlock, '更换成功态不应在表单互斥块内');
});

test('标定 Sheet：表单外壳与成功态用 wx:if/wx:else 互斥', () => {
  assert.match(sitesWxml, /<block wx:if="\{\{!calibrateSheet\.success\}\}">/, '标定表单外壳无 wx:if 控制');
  // 成功态必须是 wx:else
  const calBlockStart = sitesWxml.indexOf('<block wx:if="{{!calibrateSheet.success}}">');
  const calBlockEnd = sitesWxml.indexOf('</block>', calBlockStart);
  assert(calBlockStart > 0 && calBlockEnd > calBlockStart, '找不到标定表单 block');
  const handleInBlock = sitesWxml.slice(calBlockStart, calBlockEnd).includes('sheet-handle');
  const headerInBlock = sitesWxml.slice(calBlockStart, calBlockEnd).includes('sheet-header');
  const bodyInBlock = sitesWxml.slice(calBlockStart, calBlockEnd).includes('sheet-body');
  const footerInBlock = sitesWxml.slice(calBlockStart, calBlockEnd).includes('sheet-footer');
  assert(handleInBlock, '标定 sheet-handle 不在互斥块内');
  assert(headerInBlock, '标定 sheet-header 不在互斥块内');
  assert(bodyInBlock, '标定 sheet-body 不在互斥块内');
  assert(footerInBlock, '标定 sheet-footer 不在互斥块内');
  // 成功态是 block 后的 wx:else
  const afterBlock = sitesWxml.slice(calBlockEnd, calBlockEnd + 200);
  assert.match(afterBlock, /wx:else/, '标定成功态非 wx:else 兄弟');
  assert.match(afterBlock, /sheet-success/, '标定成功态不在 block 后');
});

test('成功态不含原生 input/textarea/picker 组件', () => {
  // 提取更换成功态 wx:else 后的 sheet-success 块内容
  const replaceSuccessStart = sitesWxml.indexOf('class="sheet-success" wx:else>');
  assert(replaceSuccessStart > 0, '找不到更换成功态');
  const replaceSuccessEnd = sitesWxml.indexOf('</view>', replaceSuccessStart + 50);
  const replaceSuccessContent = sitesWxml.slice(replaceSuccessStart, replaceSuccessEnd);
  assert.doesNotMatch(replaceSuccessContent, /<input\b/, '更换成功态含 input');
  assert.doesNotMatch(replaceSuccessContent, /<textarea\b/, '更换成功态含 textarea');
  assert.doesNotMatch(replaceSuccessContent, /<picker\b/, '更换成功态含 picker');

  // 提取标定成功态
  const calSuccessMatch = sitesWxml.match(/class="sheet-success" wx:else>[\s\S]*?<\/view>\s*<\/view>\s*<\/view>/);
  // 更简单：找第二个 sheet-success wx:else
  const secondSuccess = sitesWxml.indexOf('class="sheet-success" wx:else>', replaceSuccessStart + 100);
  assert(secondSuccess > 0, '找不到标定成功态');
  const calSuccessEnd = sitesWxml.indexOf('</view>', secondSuccess + 50);
  const calSuccessContent = sitesWxml.slice(secondSuccess, calSuccessEnd);
  assert.doesNotMatch(calSuccessContent, /<input\b/, '标定成功态含 input');
  assert.doesNotMatch(calSuccessContent, /<textarea\b/, '标定成功态含 textarea');
  assert.doesNotMatch(calSuccessContent, /<picker\b/, '标定成功态含 picker');
});

// ===== 行为测试：异步用 async IIFE =====
(async () => {
  try {
    // ===== 测试 1：同模式快速双击，旧响应不回退 =====
    {
      const d1 = deferred();
      const d2 = deferred();
      let callIdx = 0;
      api.responsibleSites = () => {
        callIdx++;
        return callIdx === 1 ? d1.promise : d2.promise;
      };
      api.reagentOverview = () => Promise.resolve({ scope: 'mine', available_scopes: ['mine', 'all'], scope_counts: { mine: 1, all: 2 }, items: [] });

      const page = makePage();
      page.data.canViewAll = true;
      page.data.scope = 'mine';

      // 第一次点击：切到 all
      page.onToggleScope();
      assert.equal(page.data.pendingScope, 'all', '点击后 pendingScope 立即设为 all');

      // 第二次点击：再切回 mine（在第一次返回前）
      page.onToggleScope();
      assert.equal(page.data.pendingScope, 'mine', '双击后 pendingScope 应为 mine（基于 pending 计算，不是已提交 scope）');

      // 让第一个请求先返回（旧请求）
      d1.resolve(makeSiteResponse('all', [{ id: 1, name: 'ALL1' }]));
      await flush();
      await flushMicro();

      // 旧请求不应修改 scope（被 scopeGeneration 拦截）
      assert.equal(page.data.scope, 'mine', '旧请求成功不应回写 scope');
      assert.equal(page.data.pendingScope, 'mine', '旧请求成功不应清零 pendingScope（还在等最新的）');

      // 让第二个请求返回（最新请求）
      d2.resolve(makeSiteResponse('mine', [{ id: 2, name: 'MINE1' }]));
      await flush();
      await flushMicro();

      assert.equal(page.data.scope, 'mine', '最新请求成功后 scope 为 mine');
      assert.equal(page.data.pendingScope, null, '最新请求成功后 pendingScope 清零');
      assert.equal(page.data.sites[0].name, 'MINE1', '展示最新请求的数据');

      passed++; tests++;
      console.log('✔ 同模式快速双击：旧响应不回退，最新意图生效');
    }

    // ===== 测试 2：跨模式切换，旧模式迟到响应不写共享 scope =====
    {
      const siteD = deferred();
      const reagentD = deferred();
      api.responsibleSites = () => siteD.promise;
      api.reagentOverview = () => reagentD.promise;

      const page = makePage();
      page.data.canViewAll = true;
      page.data.scope = 'mine';
      page._loadedScope.stations = 'mine';
      page.data.sites = [{ id: 1, name: '站点1' }];

      // 站点模式下点切换到 all（发起站点请求）
      page.onToggleScope();
      assert.equal(page.data.pendingScope, 'all');

      // 在站点请求返回前，切到试剂模式
      page.onTabReagent();
      assert.equal(page.data.activeTab, 'reagents');

      // 试剂请求返回（新模式的请求）
      reagentD.resolve(makeReagentResponse('all', [{ site_id: 1, reagent_id: 1 }]));
      await flush();
      await flushMicro();

      const scopeAfterReagent = page.data.scope;
      assert.equal(scopeAfterReagent, 'all', '试剂模式返回后 scope 为 all');
      assert.equal(page.data.pendingScope, null, '试剂模式返回后 pending 清零');

      // 现在旧的站点请求才姗姗来迟
      siteD.resolve(makeSiteResponse('all', [{ id: 99, name: '迟到站点' }]));
      await flush();
      await flushMicro();

      // 旧站点请求不应修改共享 scope（scopeGeneration 已被试剂模式递增）
      assert.equal(page.data.scope, 'all', '旧站点请求不应修改 scope（已是 all，但如果改成 mine 就错了）');
      // 更关键：sites 数据不应该被旧请求覆盖
      // 但当前在试剂模式，sites 是旧的 mine 数据，旧请求不应把 sites 改成 all 的数据
      // 验证：siteD resolve 后 sites 是否仍为旧数据
      assert.equal(page.data.sites[0].id, 1, '旧站点请求不应覆盖 sites 数据（已过期代次）');
      assert.notEqual(page.data.sites[0].name, '迟到站点', '旧站点请求数据不应写入');

      passed++; tests++;
      console.log('✔ 跨模式切换：旧模式迟到响应不写共享状态');
    }

    // ===== 测试 3：最新请求失败 → 清 pending、保留已提交范围 =====
    {
      const d = deferred();
      api.responsibleSites = () => d.promise;
      api.reagentOverview = () => Promise.resolve({ scope: 'mine', available_scopes: ['mine', 'all'], scope_counts: { mine: 1, all: 2 }, items: [] });

      const page = makePage();
      page.data.canViewAll = true;
      page.data.scope = 'mine';
      page._loadedScope.stations = 'mine';
      page.data.sites = [{ id: 1, name: '旧站点' }];

      // 点击切换到 all
      page.onToggleScope();
      assert.equal(page.data.pendingScope, 'all');

      // 请求失败
      d.reject({ status: 500, message: '服务器错误' });
      await flush();
      await flushMicro();

      assert.equal(page.data.pendingScope, null, '失败后 pendingScope 清零');
      assert.equal(page.data.scope, 'mine', '失败后保留已提交 scope（mine）');
      assert.ok(page.data.error, '失败后应有错误提示');
      assert.equal(page.data.sites[0].name, '旧站点', '失败后保留旧数据可见');

      passed++; tests++;
      console.log('✔ 最新请求失败：清 pending、保留已提交范围和旧数据');
    }

    // ===== 测试 4：切回有缓存的模式，范围匹配可复用 =====
    {
      cachedUser = { role: 'admin', capabilities: { station_monitoring_public: false } };
      let reagentCallCount = 0;
      api.responsibleSites = () => Promise.resolve(makeSiteResponse('all', [{ id: 1 }]));
      api.reagentOverview = () => {
        reagentCallCount++;
        return Promise.resolve(makeReagentResponse('all', [{ site_id: 1 }]));
      };

      const page = makePage();
      page.data.canViewAll = true;
      page.data.scope = 'all';

      // 先加载站点模式 all
      page.onShow();
      await flush();
      assert.equal(page.data.scope, 'all');
      assert.equal(page._loadedScope.stations, 'all');

      // 切到试剂模式（发起请求）
      page.onTabReagent();
      await flush();
      assert.equal(reagentCallCount, 1, '试剂模式第一次加载调用 API');
      assert.equal(page._loadedScope.reagents, 'all');

      // 切回站点模式（all 范围匹配，应复用不请求）
      const sitesBefore = page.data.sites.length;
      page.onTabSites();
      await flush();
      // sites 数据应该还在（复用缓存）
      assert.ok(page.data.sites.length > 0, '切回站点模式，缓存复用');

      // 再切回试剂模式（all 范围匹配，应复用）
      const groupsBefore = page.data.siteGroups.length;
      page.onTabReagent();
      await flush();
      assert.equal(reagentCallCount, 1, '范围匹配时不重新请求试剂 API');

      passed++; tests++;
      console.log('✔ 切回有缓存模式：范围匹配直接复用，不重新请求');
    }

    // ===== 测试 5：试剂范围变化不污染站点模式范围 =====
    {
      cachedUser = { role: 'admin', capabilities: { station_monitoring_public: false } };
      let sitesCallCount = 0;
      let reagentCallCount = 0;
      api.responsibleSites = (opts) => {
        sitesCallCount++;
        return Promise.resolve(makeSiteResponse(opts.scope, [{ id: sitesCallCount }]));
      };
      api.reagentOverview = (opts) => {
        reagentCallCount++;
        return Promise.resolve(makeReagentResponse(opts.scope, [{ site_id: reagentCallCount }]));
      };

      const page = makePage();
      page.data.canViewAll = true;

      // 站点模式加载 mine
      page.onShow(); // 默认 scope=mine
      await flush();
      assert.equal(page._loadedScope.stations, 'mine');
      const sitesMineCalls = sitesCallCount;

      // 切到试剂模式
      page.onTabReagent();
      await flush();
      assert.equal(page._loadedScope.reagents, 'mine');
      const reagentMineCalls = reagentCallCount;

      // 在试剂模式下切换到 all
      page.onToggleScope();
      await flush();
      assert.equal(page.data.scope, 'all');
      assert.equal(page._loadedScope.reagents, 'all');
      assert.equal(reagentCallCount, reagentMineCalls + 1, '切换范围调用了试剂 API');

      // 切回站点模式 — 恢复站点自己的 mine 范围并复用缓存
      page.onTabSites();
      await flush();
      assert.equal(sitesCallCount, sitesMineCalls, '试剂范围变化不应触发站点模式重载');
      assert.equal(page.data.scope, 'mine', '切回站点模式恢复站点自己的范围');
      assert.equal(page._loadedScope.stations, 'mine', '站点缓存范围保持不变');

      passed++; tests++;
      console.log('✔ 模式范围隔离：试剂 all 不污染站点 mine 缓存');
    }

    // ===== 测试 6：无 canViewAll 时不显示且零请求 =====
    {
      cachedUser = { capabilities: { station_monitoring_public: false } };
      let sitesCallCount = 0;
      let reagentCallCount = 0;
      api.responsibleSites = () => {
        sitesCallCount++;
        // 服务器返回只有 mine 权限
        return Promise.resolve({ scope: 'mine', available_scopes: ['mine'], scope_counts: { mine: 1 }, items: [] });
      };
      api.reagentOverview = () => {
        reagentCallCount++;
        return Promise.resolve({ scope: 'mine', available_scopes: ['mine'], scope_counts: { mine: 1 }, items: [] });
      };

      const page = makePage();
      page.data.canViewAll = false; // 初始无权限
      page.onShow();
      await flush();

      assert.equal(page.data.canViewAll, false, '服务器返回无 all 权限');
      // 尝试调用 onToggleScope
      const result = page.onToggleScope();
      assert.equal(result, false, '无权限时 onToggleScope 返回 false');
      assert.equal(sitesCallCount, 1, '无权限时 onToggleScope 不发起额外请求');
      assert.equal(reagentCallCount, 0, '无权限切换不触发试剂请求');

      // 验证 WXML 中 wx:if 依赖 canViewAll
      assert.match(sitesWxml, /wx:if="\{\{canViewAll\}\}"/, 'WXML 中范围切换受 canViewAll 控制');

      passed++; tests++;
      console.log('✔ 无 canViewAll：不显示且零请求');
    }

    // ===== 测试 7：scopeGeneration 在 onHide/onUnload 时失效 =====
    {
      const d = deferred();
      api.responsibleSites = () => d.promise;
      api.reagentOverview = () => Promise.resolve({ scope: 'mine', available_scopes: ['mine', 'all'], scope_counts: { mine: 1, all: 2 }, items: [] });

      const page = makePage();
      page.data.canViewAll = true;
      page.data.scope = 'mine';
      page.data.sites = [{ id: 1, name: '保留' }];

      page.onToggleScope();
      assert.equal(page.data.pendingScope, 'all');

      // 隐藏页面（_inactive = true，_sitesRequestId 递增）
      page.onHide();

      d.resolve(makeSiteResponse('all', [{ id: 99, name: '迟到' }]));
      await flush();
      await flushMicro();

      // _inactive 保护：请求不回写
      assert.equal(page.data.scope, 'mine', 'onHide 后请求成功不回写 scope');
      assert.equal(page.data.sites[0].name, '保留', 'onHide 后请求成功不回写数据');

      passed++; tests++;
      console.log('✔ onHide 后过期请求不修改共享状态');
    }

  } catch (e) {
    failed++;
    console.log('✘ 行为测试异常: ' + e.message);
    console.log(e.stack);
  }

  // ===== 汇总 =====
  console.log('');
  console.log('behavior tests ' + passed + '/' + tests);
  if (failed > 0) process.exit(1);
})();
