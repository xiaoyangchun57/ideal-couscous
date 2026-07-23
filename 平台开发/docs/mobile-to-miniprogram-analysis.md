# 移动端 H5 → 微信小程序 架构改造分析报告

> 生成时间：2026-07-04
> 分析范围：frontend/mobile.html（3186行）、miniprogram/、backend/app.py

---

## 一、现状总览

### 当前方案：web-view 壳子
```
miniprogram/
  app.js          → App({}) 空壳
  app.json        → 只配了 pages/index/index 一个页面
  pages/index/
    index.wxml    → <web-view src="..."/>  加载远程H5
    index.js      → Page({}) 空壳
```

**实质 = 在微信里套了个浏览器**。这是最偷懒但也是最差的方式：
- ❌ 无法用 wx 原生 API（拍照、定位、文件上传只能走 H5 方式）
- ❌ 冷启动慢（要等整个 H5 页面+所有 JS 加载完）
- ❌ 跟微信生态脱节（不能分享给好友、不能用订阅消息）
- ❌ 没有原生 TabBar 体验
- ❌ 网络请求不受 wx.request 域名白名单管控

### H5 移动端功能体量

| 模块 | 行数（约） | 关键函数 | 备注 |
|------|-----------|---------|------|
| 首页驾驶舱（首页Tab） | 500+ | init(), refreshData(), updateTaskCounter(), renderHome() | 含天气、待办、天气图标对齐 |
| 工单面板 | 150+ | renderWOPanel(), openWoDetail(), _backFromWODetail() | 进度条、状态标签 |
| 站点监测面板 & 站点列表页 | 400+ | renderSitePanel(), renderSiteList(), showSiteDetail() | 分类筛选、搜索、置顶 |
| 站点详情（内联层） | 400+ | showSiteDetail(), switchSdTab(), _renderDataChart() | 设备运行 + 站点档案双Tab |
| 预警中心 | 300+ | renderAlertList(), filterAlerts(), showAlertDetail() | 批量办结、批量转工单 |
| 巡检执行引擎 | 600+ | openInspExec(), renderInspExec(), markItem(), markCatAllNormal() | 分类折叠、批量提交、拍照 |
| 备件申请 | 200+ | openPartsApply(), submitParts() | 站点搜索、部件分类 |
| 运维规范 | 200+ | renderManuals(), toggleManual() | 手风琴式分类浏览 |
| 全局搜索 | 150+ | globalSearch() | 跨模块搜索 |
| 认证层 | 100+ | doLogin(), checkAuth(), doLogout() | Token + localStorage |
| CSS 样式 | 420+ | 全部内联 `<style>` | WeUI 风格、暗黑模式 |
| 图表 | 80+ | _renderDataChart() | Chart.js line chart |
| SVG 图标 | 50+ | ICONS 对象 | Lucide 风格 SVG |
| 全局状态 | — | 全部 global var（allSites/allWO/allAlerts/allInsp...） | 无状态管理 |

---

## 二、微信小程序与 H5 的核心差异点

### 2.1 渲染层差异（最根本的）

| H5 | 小程序 |
|-----|--------|
| DOM 树（html/body/div） | WXML 节点树（view/text/image） |
| 可以 document.getElementById/innerHTML | ❌ 禁止一切 DOM/BOM 操作 |
| CSS（标准 CSSOM） | WXSS（子集，不支持 body/html/通配伪类） |
| 可以直接操作 \<canvas\> | 要用 wx.createCanvasContext |
| SVG 内联支持良好 | ❌ 小程序 SVG 支持非常有限 |

**影响**：mobile.html 中几乎所有函数都用了 `document.getElementById()` 和 `.innerHTML`，这些在 WXML 里全部要改成 `this.setData()` + 条件渲染。

### 2.2 网络请求差异

| H5 | 小程序 |
|-----|--------|
| fetch() / XMLHttpRequest | wx.request() |
| AbortController 超时 | 直接传 timeout 参数 |
| 401 自行判断 | wx.request 的 fail/success 回调 |
| 无域名限制 | **必须配置域名白名单**（mp.weixin.qq.com 后台） |

### 2.3 存储差异

| H5 | 小程序 |
|-----|--------|
| localStorage / sessionStorage | wx.setStorageSync / wx.getStorageSync |
| 容量 ~5MB | 容量 ~10MB |
| Token 手动管理 | 推荐 wx.login 获取 code→换取 session |

### 2.4 文件/图片差异

| H5 | 小程序 |
|-----|--------|
| `<input type="file" accept="image/*">` | wx.chooseImage() / wx.chooseMedia() |
| FileReader / canvas.toBlob() | wx.compressImage() 内置 |
| FormData 上传 | wx.uploadFile() |
| `<img>` 标签 | `<image>` 组件（支持 lazy-load、mode） |

### 2.5 导航差异

| H5 | 小程序 |
|-----|--------|
| div 显示/隐藏切换页面 | wx.switchTab / wx.navigateTo / wx.redirectTo |
| 内联层（.inline-view） | 独立 Page 或 Component |
| 底部 TabBar 用 div 模拟 | app.json 配置原生 TabBar |
| URL hash/routing | 页面栈管理（最多10层） |

### 2.6 认证差异

| H5 | 小程序 |
|-----|--------|
| 用户名+密码 → fetch登录 → 返回 token | wx.login() → code → 后端换 session → 前端存 storage |
| 可自定义登录页 | 可自定义，但推荐静默登录 |

---

## 三、改造路径对比：三个方案

### 方案 A：优化现有 web-view（轻量）

在现有壳子基础上做最小改动：

```
改动量：★☆☆☆☆
维护性：★★☆☆☆
体验：★★☆☆☆
```

**要做的事：**
1. 在 web-view 页面内引入 WeChat JS-SDK（通过 JSSDK 使用 wx.chooseImage/wx.getLocation 等）
2. 优化 H5 加载速度（分包、懒加载、预加载）
3. 配置导航栏和分享

**致命伤**：依然不能使用 wx 原生 API 的完整能力，包大小和启动速度瓶颈无解。

### 方案 B：完整原生重写（推荐，但分阶段）

将每个 H5 页面/模块逐一重写为原生小程序页面：

```
改动量：★★★★★
维护性：★★★★★
体验：★★★★★
```

**注意**：不建议一锅端，应该分阶段增量替换。

### 方案 C：混合模式（最佳实践）

第一阶段保持 web-view 兜底，第二阶段原生重写高频页面。

```
改动量：★★★☆☆（逐步）
维护性：★★★★☆
体验：★★★★☆
```

**推荐路径：**
- Phase 1：核心功能（首页驾驶舱、预警中心、站点列表）→ 原生
- Phase 2：巡检执行引擎 → 原生（最复杂但最需要性能）
- Phase 3：工单管理、备件申请 → 原生
- Phase 4：运维规范、搜索 → 原生
- 保留 web-view 作为退路，灰度切换

---

## 四、详细架构设计方案

### 4.1 小程序目录结构

```
miniprogram/
├── app.js                    # 全局逻辑 + 启动初始化
├── app.json                  # 全局配置 + TabBar + 页面路由
├── app.wxss                  # 全局样式 token
├── project.config.json
├── sitemap.json
│
├── pages/
│   ├── index/                # 首页驾驶舱（home tab）
│   │   ├── index.js
│   │   ├── index.json
│   │   ├── index.wxml
│   │   └── index.wxss
│   ├── sites/                # 站点管理页
│   ├── alerts/               # 预警中心
│   ├── site-detail/          # 站点详情（独立页面）
│   ├── alert-detail/         # 预警详情
│   ├── work-order/           # 工单列表 & 详情
│   ├── insp-exec/            # 巡检执行引擎（最重度）
│   ├── manuals/              # 运维规范
│   ├── parts-apply/          # 备件申请
│   └── search/               # 全局搜索
│
├── components/               # 复用组件
│   ├── status-badge/         # 状态标签
│   ├── site-card/            # 站点卡片
│   ├── work-order-card/      # 工单卡片
│   ├── alert-card/           # 预警卡片
│   ├── empty-state/          # 空状态
│   ├── loading/              # 加载状态
│   └── chart-line/           # 简易折线图（canvas）
│
├── utils/
│   ├── request.js            # wx.request 统一封装（auth + 超时）
│   ├── auth.js               # wx.login + 登录态管理
│   ├── storage.js            # Storage 封装
│   ├── format.js             # 日期/数字格式化（从 utils.js 移植）
│   └── constants.js          # 常量映射（从 shared/constants.js 移植）
│
├── images/                   # 本地图标资源（SVG→png/本地化）
│   └── icons/
│
└── subpackages/              # 低频功能子包
    ├── history/              # 历史记录
    └── report/               # 报表
```

### 4.2 app.json 配置

```json
{
  "pages": [
    "pages/index/index",
    "pages/sites/index",
    "pages/alerts/index",
    "pages/site-detail/index",
    "pages/alert-detail/index",
    "pages/work-order/index",
    "pages/insp-exec/index",
    "pages/manuals/index",
    "pages/parts-apply/index",
    "pages/search/index"
  ],
  "subPackages": [
    {
      "root": "subpackages/history/",
      "pages": ["index"]
    },
    {
      "root": "subpackages/report/",
      "pages": ["index"]
    }
  ],
  "window": {
    "navigationBarTitleText": "水文运维",
    "navigationBarBackgroundColor": "#07C160",
    "navigationBarTextStyle": "white",
    "backgroundColor": "#f5f5f5"
  },
  "tabBar": {
    "color": "#999",
    "selectedColor": "#07C160",
    "backgroundColor": "#ffffff",
    "borderStyle": "black",
    "list": [
      {
        "pagePath": "pages/index/index",
        "text": "首页",
        "iconPath": "images/icons/home.png",
        "selectedIconPath": "images/icons/home-active.png"
      },
      {
        "pagePath": "pages/sites/index",
        "text": "站点",
        "iconPath": "images/icons/sites.png",
        "selectedIconPath": "images/icons/sites-active.png"
      },
      {
        "pagePath": "pages/alerts/index",
        "text": "预警",
        "iconPath": "images/icons/alerts.png",
        "selectedIconPath": "images/icons/alerts-active.png"
      }
    ]
  },
  "requiredPrivateInfos": ["getLocation"],
  "permission": {
    "scope.userLocation": {
      "desc": "用于巡检签到时获取当前位置"
    }
  }
}
```

### 4.3 核心 API 请求封装

```javascript
// utils/request.js - 替换 H5 的 af() / afP() / afPt()
const BASE_URL = 'https://your-domain.com/api';

const request = (options) => {
  return new Promise((resolve, reject) => {
    const token = wx.getStorageSync('water_ops_token');

    wx.request({
      url: BASE_URL + options.url,
      method: options.method || 'GET',
      data: options.data,
      header: {
        'Content-Type': 'application/json',
        'Authorization': token ? `Bearer ${token}` : '',
        ...options.header,
      },
      timeout: options.timeout || 30000,
      success: (res) => {
        if (res.statusCode === 401) {
          // 跳转登录页
          wx.removeStorageSync('water_ops_token');
          wx.navigateTo({ url: '/pages/login/index' });
          reject({ code: 401, message: '未登录' });
          return;
        }
        resolve(res.data);
      },
      fail: (err) => {
        reject({ code: -1, message: '网络错误', detail: err });
      },
    });
  });
};

module.exports = { request };
```

### 4.4 认证适配

```javascript
// utils/auth.js
const { request } = require('./request');

// 微信静默登录 → 服务端交换 session
const wechatLogin = async () => {
  try {
    const { code } = await wx.login();
    const res = await request({
      url: '/auth/wechat-login',
      method: 'POST',
      data: { code },
    });
    if (res.token) {
      wx.setStorageSync('water_ops_token', res.token);
      return res.user;
    }
    // 如果微信登录失败，降级到 H5 的账号密码登录
    return null;
  } catch (e) {
    return null;
  }
};

// 账号密码登录（H5 兼容，作为微信登录的降级）
const passwordLogin = async (username, password) => {
  const res = await request({
    url: '/auth/login',
    method: 'POST',
    data: { username, password },
  });
  if (res.token) {
    wx.setStorageSync('water_ops_token', res.token);
    return res.user;
  }
  throw new Error(res.error || '登录失败');
};

module.exports = { wechatLogin, passwordLogin };
```

### 4.5 关键功能对照改造表

| H5 实现 | 小程序实现 | 关键 API |
|---------|-----------|---------|
| `document.getElementById('xxx')` | `this.setData({ xxx: value })` + WXML 绑定 | — |
| `el.innerHTML = html` | WXML `wx:for` + 组件化渲染 | — |
| `el.classList.add/remove` | `wx:if` / `hidden` 属性 | — |
| `localStorage.getItem/setItem` | `wx.getStorageSync/setStorageSync` | Storage API |
| `fetch('/api/sites')` | `wx.request({ url: BASE + '/sites' })` | Request |
| `<input type="file">` 拍照 | `wx.chooseImage()` / `wx.chooseMedia()` | Media |
| `fetch POST /upload` FormData | `wx.uploadFile()` | Upload |
| `navigator.geolocation` | `wx.getLocation()` | Location |
| `<canvas>` + Chart.js | 小程序 Canvas 2D + 简易绘图 | Canvas |
| `<svg>` 图标 | `<image>` 引用 png 或 iconfont | — |
| `setInterval(30s 刷新)` | `wx.onAppShow` + `wx.setTabBarBadge` | Lifecycle |
| 下拉刷新（自定义 touch） | `enablePullDownRefresh` + `onPullDownRefresh` | Page |
| window.open 导航 | `wx.openLocation` / `wx.getLocation` | Location |
| 登录页弹窗 | 独立 Page 或 Component | — |

### 4.6 巡检执行引擎——改造重点

这是 mobile.html 最复杂的模块（600+行），也是巡检验收的核心工作流：

```javascript
// pages/insp-exec/index.js 核心骨架
Page({
  data: {
    plan: null,
    categories: {},      // { '设备检查': { items: [...], collapsed: false, done: 3, total: 5 } }
    progress: 0,         // 完成百分比
    progressLabel: '0/0',
    allDone: false,
  },

  onLoad(options) {
    this.planId = options.plan_id;
    this.loadPlan();
  },

  async loadPlan() {
    const { request } = require('../../utils/request');
    const data = await request({ url: `/inspection-v2/plans/${this.planId}` });
    // 按 category 分组 → setData
  },

  // 标记单项正常/异常
  markItem(e) {
    const { itemId, result } = e.currentTarget.dataset;
    // wx.request → update local state → setData
  },

  // 批量标记正常
  markCatAllNormal(e) {
    const catName = e.currentTarget.dataset.cat;
    // 串行调用 wx.request → 全部完成后 setData 刷新
  },

  // 拍照
  takePhoto(e) {
    wx.chooseImage({
      count: 1,
      sourceType: ['camera', 'album'],
      success: (res) => {
        wx.uploadFile({...});
      }
    });
  },
});
```

### 4.7 图表适配

H5 用了 Chart.js，小程序必须换方案：

| 方案 | 优劣 |
|------|------|
| **Canvas 2D 原生绘制** | ✅ 无依赖、包小；❌ 实现曲线图要手写 |
| **echarts-for-weixin** | ✅ 功能强，跟 Chart.js 最接近；❌ 包较大（~500KB），建议放子包 |
| **wx-charts（第三方轻量）** | ✅ 包小；❌ 更新不活跃 |

**建议**：首页概览用原生 Canvas 2D 画简化的折线图，站点详情里也走同一套，避免包体膨胀。如果以后需要复杂的统计图，用 echarts-for-weixin 放子包。

### 4.8 后端需要新增的接口

当前后端已经有一套完整的 REST API（约100+端点），小程序复用没问题。但需要新增：

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/auth/wechat-login` | POST | 微信 code → session 登录 |
| `/api/auth/wechat-login` 支持返回 `token` 字段 | — | 已有 `/api/auth/login` 但小程序需要 code 交换 |

**最小改动**：在现有的 `/api/auth/login` 上增加 `code` 字段支持，前端根据运行环境（H5 vs 小程序）决定使用哪种登录方式。

---

## 五、性能与合规注意事项

### 5.1 包大小控制

```
主包限制：2MB（包含 app.js + tabBar 页面 + 公共组件/工具）
总限制：20MB（含子包）
```

**策略：**
- 主包放 3 个 Tab 页面 + 公共组件 + utils
- 巡检执行引擎（重量级）放子包
- 运维规范数据（硬编码的 ~200 条）放子包或网络加载
- SVG 图标全部转成 20x20@2x png，放 images/ 目录

### 5.2 域名白名单

必须在小程序后台配置以下域名：
- `https://your-api-domain.com`（API 服务器）
- `https://your-upload-domain.com`（文件上传）
- `https://webview-domain.com`（web-view 回退）

### 5.3 审核注意事项

1. **定位权限**：在 app.json 声明 `requiredPrivateInfos: ['getLocation']`，且必须在小程序页面中有实际的定位使用场景（巡检签到）
2. **用户隐私**：如果需要获取用户信息（昵称、头像），必须用新版 `wx.getUserProfile` 或头像昵称填写能力
3. **登录规范**：不能强制用户手机号登录，必须提供"跳过"选项
4. **内容安全**：用户上传的图片需调用 `wx.sec.imgSecCheck` 审核

### 5.4 启动速度优化

```
当前 H5 web-view 方案：
  用户点击 → 微信加载 web-view → H5 下载 mobile.html(200K+) + 外部JS → 渲染 → API请求 → 首页展示
  预计：2-5秒

原生方案：
  用户点击 → 微信加载首页 Page → wx.request 请求 → setData 渲染
  预计：0.5-1.5秒
```

**具体措施：**
- 分包预加载：`app.json` 的 `preloadRule` 配置子包预下载
- 首页请求并行化：`/sites`、`/workorders?limit=50`、`/alerts?limit=200`、`/inspection-v2/plans` 四条请求同时发出
- `onLoad` 阶段先展示骨架屏，数据到了再填充
- 数据缓存：将站点列表、工单列表缓存到 storage，下次启动先展示缓存数据，后台静默刷新

---

## 六、分阶段实施路线图

```
Phase 1（2周）
┣━ 搭建小程序骨架：app.json + tabBar + 路由 + utils
┣━ 实现 首页驾驶舱（Tab1）— wx.request + setData 渲染
┣━ 实现 站点列表页（Tab2）— 分类筛选 + 搜索
┣━ 实现 预警中心（Tab3）— 列表 + 状态筛选 + 批量操作
┗━ 后端新增 /api/auth/wechat-login

Phase 2（2周）
┣━ 站点详情（独立 Page）— 设备运行Tab + 站点档案Tab
┣━ 预警详情（独立 Page）— 办结/转工单/督办
┣━ 工单列表 & 详情（独立 Page）
┗━ 登录适配（微信登录 + 账号密码降级）

Phase 3（2周）
┣━ 巡检执行引擎（子包）— 分类折叠 + 逐项标记 + 批量操作
┣━ 拍照上传（wx.chooseImage + wx.uploadFile）
┣━ Canvas 简易图表
┗━ 拼音搜索 / 全局搜索

Phase 4（1周）
┣━ 备件申请 & 运维规范
┣━ 性能优化 + 缓存策略
┣━ 异常处理 + 网络重试
┗━ 审核材料准备 + 提审
```

---

## 七、总结

| 维度 | 当前 web-view 方案 | 原生方案 |
|------|------------------|---------|
| 启动速度 | 2-5s（加载远程H5） | 0.5-1.5s |
| 拍照上传 | H5 File API（兼容性差） | wx.chooseImage + wx.uploadFile（流畅） |
| 定位签到 | navigator.geolocation | wx.getLocation（精准） |
| 分享能力 | 无 | onShareAppMessage / onShareTimeline |
| 订阅消息 | 无 | wx.requestSubscribeMessage |
| 离线能力 | 无 | Storage 缓存 + 预加载 |
| TabBar 体验 | div 模拟 | 原生 TabBar |
| 包大小 | ~250K（全量加载） | ~1.5MB（可分包按需加载） |
| 开发维护 | 单文件 3000+ 行（难维护） | 多页面组件化（易维护） |
| 微信审核 | 不需要 | 需要（每次更新提审） |
| 用户感知 | "像浏览器里的网页" | "像微信里的原生应用" |

**结论：完全值得也建议改造成原生小程序**。建议 Phase 1 先把 TabBar 三个核心页面重写，验证体验优势后再逐步推进 Phase 2-4。
