# 水质智慧运维 · 设计系统

> 参考 Apple Human Interface Guidelines 的水质运维设计系统
> 主要覆盖：微信小程序现场端 · React Web 管理端
> 版本：1.2.1
> 更新：接入长期协作矩阵，修正五 Tab、双端范围与设计/开发交付边界

本文件及同目录六份规范是远程仓库内的设计规则入口。规则不覆盖当前 `CURRENT` 文件中的业务范围、权限、接口和状态迁移；冲突时先由产品与对应域负责人裁决。本机 `.design_library` 中的组件 JSON、预览页和源 Token 目前不属于本次提交，远程执行不得把这些未入库资产当作完成任务的前置条件。

---

## 为什么需要设计系统？

页面级交付只能覆盖已设计的场景。开发遇到新页面、新组件时，只能凭感觉实现，导致风格不一致。

**设计系统 = 设计规则的代码化**，开发新增功能时直接从组件库中取用，不需要自己设计样式，确保整个产品视觉统一。

---

## 设计系统包含什么？

```
.design_library/水质运维-Apple/（本机设计源资产，当前未入库）
├── colors_and_type.css     # Token 文件（颜色/字体/间距/圆角/阴影）
├── css.json                # Token JSON 格式（供 JS/TS 调用）
├── icons/                  # 图标库（SVG 格式，42 个）
│   ├── arrow-*.svg
│   ├── chevron-*.svg
│   └── ...（共 42 个线性图标）
├── components/             # 组件规格（JSON 定义）
│   ├── index.json          # 组件索引
│   ├── button.json
│   ├── nav-bar.json
│   └── ...（共 20 个核心组件）
├── preview/                # 组件预览页
│   └── component-*.html
├── ui_kits/
│   └── mobile/index.html   # 组件库可视化展示页
└── README.md               # 本文档
```

---

## 核心原则（铁律）

### 1. 类型不绑颜色，状态才绑颜色

- **业务类型标签一律中性灰**：如"巡检"、"工单"、"周检"、"水厂"、"监测站"等
- **状态只用 5 个语义色**：
  - 🟢 绿 = 成功 / 正常 / 已完成 / 已通过
  - 🔵 蓝 = 进行中 / 信息提示
  - 🟡 黄 = 提醒 / 提示级（告警四级体系）
  - 🟠 橙 = 等待用户动作 / 待审核 / 待审批 / 待补拍
  - 🔴 红 = 异常 / 失败 / 危险 / 驳回 / 删除
- “运行中”属于进行中，用蓝色；“处理中”只有在等待用户或审核动作时用橙色，不再映射到成功或进行中。

### 2. 禁止卡片左侧彩色色条

不要用 `border-left` 给卡片加彩色侧边条来区分类型。状态用状态标签（Chip）表达。

### 3. 主按钮统一蓝色

"通过"、"批准"、"完成"等确认动作也是蓝色主按钮，绿色只用于状态标签，不用于按钮。

### 4. 红色按钮仅用于破坏性操作

删除、驳回、退回等不可逆操作用红色描边按钮。

### 5. 最小点击区域 44×44px

所有可点击元素的触控区域不小于 44px（iOS 标准）。

---

## Token 使用指南

### CSS 变量

```css
/* 引入 Token */
@import './colors_and_type.css';

/* 组件只消费语义化 Token */
.my-button {
  background: var(--color-primary);
  color: var(--color-primary-foreground);
  border-radius: var(--radius-lg);
  padding: 0 var(--space-4);
  height: var(--button-height-lg);
}
```

**不要直接使用原始色板**（如 `var(--palette-brand-500)`），始终通过语义层消费。

### JS/TS 中使用

```js
import tokens from './css.json';

const primaryColor = tokens.tokens['color-primary'];
```

### Token 层级

```
Palette（原始色板）← 不直接消费
    ↓
Semantic（语义层）← 组件直接消费
    ↓
Component（组件层）← 组件专用变量，从语义层派生
```

---

## 图标库

### 图标风格

统一使用线性风格图标（Lucide / SF Symbols 风格），24×24 标准画布，2px 线宽，圆角端点。所有图标使用 `currentColor` 填充，可通过 CSS `color` 属性直接控制颜色。

### 使用规范

| 属性 | 规范 |
|------|------|
| 标准尺寸 | 24px（24×24 画布） |
| 常用尺寸 | 16px / 18px / 20px / 24px |
| 颜色 | 通过 `color` 属性控制，使用语义色 token（`--color-icon-primary` 等） |
| 状态 | 图标颜色随交互状态变化，不单独做彩色版本 |
| 线宽 | 统一 2px，不随尺寸缩放改变线宽 |

**CSS 控制颜色示例：**
```css
.icon {
  width: 20px;
  height: 20px;
  color: var(--color-icon-secondary);
}
.icon:active {
  color: var(--color-primary);
}
```

### 图标分类（共 42 个）

**导航与方向**
- `arrow-up` / `arrow-down` / `arrow-left` / `arrow-right` — 箭头方向
- `chevron-up` / `chevron-down` / `chevron-left` / `chevron-right` — 雪佛龙箭头（列表行右箭头等）
- `chevrons-up-down` / `chevrons-down-up` — 双向箭头（排序、展开收起）
- `external-link` — 外部链接

**操作与动作**
- `circle-plus` — 添加、新建
- `circle-minus` — 减少、移除
- `check` / `circle-check` — 勾选、完成、通过
- `circle-alert` / `triangle-alert` — 警告、注意
- `circle-play` / `circle-pause` — 播放/暂停
- `circle-question-mark` — 帮助、疑问
- `trash-2` — 删除
- `pen-line` — 编辑
- `send-horizontal` — 发送
- `mouse-pointer-click` — 点击、交互
- `thumbs-up` / `thumbs-down` — 赞/踩
- `heart` — 收藏、关注
- `star` — 星标
- `grip` — 拖拽把手
- `funnel` — 筛选

**内容与文件**
- `file` — 文件
- `folder` / `folder-open` — 文件夹
- `box` — 包裹、备件
- `tag` — 标签
- `clipboard-check` — 巡检、任务清单、已完成的剪贴板

**地点与导航**
- `house` — 首页、主页
- `map-pin` — 位置、站点
- `bell` — 告警、通知、消息铃铛

**通讯与消息**
- `mail` — 消息、邮件
- `message-circle-more` — 更多消息

**用户与身份**
- `user` — 用户、人员

### 小程序适配

| 场景 | 方案 |
|------|------|
| 组件内图标（列表行、按钮等） | SVG 内联或 base64 背景图，用 `currentColor` 控色 |
| TabBar 图标 | 必须用本地 PNG，小程序原生 TabBar 不支持 SVG；当前源图使用 81×81px 画布并对齐现有图标视觉范围 |
| 空状态大图标 | 可用 SVG 或 PNG，建议 64px 尺寸 |
| 按钮图标 | 内联 SVG，与文字同行对齐 |

---

## 组件使用指南

### 底部动作栏 Action Bar

底部固定操作栏，承载页面主动作。
- 4 种变体：单按钮 / 双按钮主次 / 三图标按钮 / 提示条+按钮
- 必须适配底部安全区
- 一个页面最多一个 primary 主按钮，主按钮在右侧
- 按钮统一 lg 尺寸（48px）

### 底部面板 Bottom Sheet

从底部滑出的面板容器，承载表单、详情、快捷操作等较复杂内容。
- 4 种变体：纯内容 / 表单 / 详情 / 快捷操作宫格
- 顶部必须有把手条（36px × 4px）
- 最大高度 75vh，内部滚动
- 底部按钮适配安全区
- 与 Action Sheet 的区别：Bottom Sheet 承载复杂内容，Action Sheet 仅承载简单选项列表

### 快捷入口 Quick Actions

卡片内的轻量操作集合，以文字+分隔线宫格呈现。
- 3 种变体：四列宫格 / 双行宫格 / 三列宫格
- 入口之间用 0.5px 细线分隔，不用卡片或按钮形态
- 每项高度 64px，按下态用灰底反馈
- 不超过 8 个入口，过多改用列表导航

### 进度指示器 Progress

展示可量化进度的组件，支持条形和环形。
- 3 种变体：条形 / 条形带文字 / 环形
- 4 种语义色：primary（进行中）/ success（完成）/ warning（待处理）/ error（异常）
- 条形高度 6px，环形直径 80px
- 进度颜色只用语义色，不绑定业务类型

### 按钮 Button

| 变体 | 使用场景 |
|------|----------|
| Primary（蓝色填充） | 页面核心操作，如"提交"、"确认"、"下一步"、"完成巡检" |
| Secondary（白底描边） | 次要操作、取消 |
| Destructive（红描边） | 删除、驳回、退回等破坏性操作 |
| Text（纯文字） | 更多、查看详情、编辑等轻量操作 |
| Ghost（灰底） | 底部动作栏次按钮、表单取消按钮 |
| Full-Width（全宽） | 底部动作栏、表单提交、空状态操作按钮 |

- 一个页面最多一个主按钮
- 按钮高度：sm=32px / md=40px / lg=48px
- 底部操作栏中的按钮用 lg 尺寸
- 按钮字重统一 500
- **铁律：禁止绿色填充按钮**，绿色只用于状态标签
- **小程序**：使用 `<button>` 组件保留原生能力；必须去除 `::after` 默认边框

### 导航栏 Nav Bar

| 变体 | 使用场景 |
|------|----------|
| Compact（紧凑） | 二级/三级页、列表 Tab 页（巡检计划、告警） |
| Large Title（大标题） | 首页、我的等内容型 Tab 根页 |

- 紧凑导航栏有底部分隔线，大标题导航栏没有
- 返回按钮始终是蓝色 "< 返回"
- 右侧操作按钮不超过 2 个

### 底部标签栏 Tab Bar

- 目标结构固定 5 个 Tab：首页 / 巡检 / 站点 / 告警 / 我的
- 巡检 Tab 默认进入巡检计划列表（plans），不是打卡页
- 子流程页面（打卡、拍照、提交）不显示 TabBar
- **命名铁律**：`data-nav-key` 和 `data-dom-id` 必须与目标页面 ID 可直接对应

### 状态标签 Status Chip

**6 种类型 + 3 种变体，不可自行新增：**

| 类型 | 颜色 | 用途示例 |
|------|------|----------|
| success | 绿 | 正常、已完成、已通过 |
| warning | 橙 | 等待用户动作、待审核、待审批、待补拍、待整改 |
| error | 红 | 异常、紧急、已驳回、失败 |
| info | 蓝 | 进行中、信息提示 |
| notice | 黄 | 告警四级中的提示/通知级，严重程度低于 warning |
| default | 灰 | **所有业务类型标签**（巡检、工单、周检、水厂...） |

**3 种变体**：胶囊形（默认）/ 全圆角 / 带图标

- **铁律：待审核/待补拍用橙色（warning），不用蓝色（info）**
- 标签尺寸：11px 字 + 2px 上下内边距 + 8px 左右内边距

### 列表行 List Row

- 标准高度 44px（iOS 标准）
- 左侧图标 18px，颜色用 `--color-icon-secondary`
- 右侧箭头颜色用 `--palette-gray-400`
- 分隔线从左侧 16px 处开始（不是整宽）

### 分组卡片 Card

- 圆角 16px，白底
- 页面左右边距 16px
- 卡片间距 10px
- Section 标题：13px、三级文字色、上间距 20px、下间距 6px

### 间距与字距

- 使用 2px 基础步进，8px 为主节奏；页面边距和卡片间距优先落在 8px 的倍数上。
- 全部字距为 `0`，不使用负字距或展示性字距。

### 分段控件 Segmented Control

- 高度 32px，圆角 8px
- 不超过 4 段
- 激活项白底 + 轻微阴影
- 未激活项灰底

---

## 团队协作文档

> 各执行单元端到端协作的统一设计口径。
> 做页面前先看铁律，做完后按清单自查，对接开发时按规范接线。

| 文档 | 一句话说明 | 什么时候看 |
|------|-----------|-----------|
| [DESIGN-RULES.md](./DESIGN-RULES.md) | 10 条设计铁律，违反即打回 | 写代码前扫一眼 / Review 时对照 |
| [DESIGN-CHECKLIST.md](./DESIGN-CHECKLIST.md) | 按页面类型的自检清单 | 做完页面后逐项检查 |
| [COMPONENT-DECISION-TREE.md](./COMPONENT-DECISION-TREE.md) | 组件选型决策树 | 新需求不知道用哪个组件时 |
| [DEV-HANDOFF.md](./DEV-HANDOFF.md) | 开发接线规范（状态枚举/字段映射/Token 纪律） | 写代码前 / 跟后端对接口时 |
| [DELIVERY-STANDARD.md](./DELIVERY-STANDARD.md) | 交付格式标准 + 验收清单 | 功能完成交付时 |
| [CONTRIBUTING.md](./CONTRIBUTING.md) | 设计贡献指南（新增组件/Token 流程） | 需要扩展设计系统时 |

**快速上手路径**：
1. 新人入职 → 先读 README + DESIGN-RULES（10 分钟）
2. 拿到新需求 → 查 COMPONENT-DECISION-TREE 选组件
3. 页面写完 → 按 DESIGN-CHECKLIST 自查
4. 对接后端 → 按 DEV-HANDOFF 对齐状态枚举和字段
5. 交付上线 → 按 DELIVERY-STANDARD 确认交付物齐
6. 想加新组件/Token → 读 CONTRIBUTING 走流程

---

## 设计模式指南

### 页面布局模板

```
┌─────────────────────────┐
│  状态栏                  │
├─────────────────────────┤
│  导航栏 / 大标题区域      │
├─────────────────────────┤
│  [可选] 分段控件          │
├─────────────────────────┤
│  Section 标题           │
│  ┌───────────────────┐  │
│  │  分组卡片          │  │
│  │  - 列表行          │  │
│  │  - 列表行          │  │
│  └───────────────────┘  │
│                          │
│  Section 标题           │
│  ┌───────────────────┐  │
│  │  分组卡片          │  │
│  └───────────────────┘  │
└─────────────────────────┘
```

### 列表页标准结构

1. 导航栏（返回 + 标题 + 右侧操作）
2. [可选] 分段控件筛选
3. [可选] 搜索栏
4. 列表内容（卡片 + 列表行 或 整宽列表）
5. 空状态（无数据时）
6. [可选] 底部操作栏

### 表单页标准结构

1. 导航栏（返回 + 标题 + 保存/提交）
2. 分组卡片（表单字段）
3. 字段标签在上，输入框在下
4. 必填项用红星标记
5. 错误提示在字段下方，红色 12px
6. 底部操作栏（提交按钮）

### 详情页标准结构

1. 导航栏（返回 + 标题）
2. 信息卡片（基本信息）
3. 状态标签 + 关键数据
4. 时间线（操作历史）
5. 照片附件
6. 底部操作栏

### 空状态处理

| 场景 | 组件 | 是否有操作按钮 |
|------|------|---------------|
| 列表为空，可主动添加 | Empty State (no-data) | 是（添加按钮） |
| 搜索无结果 | Empty State (no-result) | 否 |
| 加载失败 | Empty State (error) | 是（重新加载） |
| 无权限 | Empty State (permission) | 否 |

### 加载状态

- 首次加载：全屏骨架屏或 Loading 指示器
- 上拉加载更多：底部 Spinner + "加载中..."
- 下拉刷新：系统原生刷新控件

---

## 微信小程序适配指南（必读）

本项目以微信小程序为主要平台，小程序的导航机制与原生 App 有本质区别。**所有页面设计必须考虑以下约束。**

### 导航栏

#### 两种方案选择

| 方案 | 优点 | 缺点 | 适用场景 |
|------|------|------|----------|
| **微信原生导航栏** | 开发成本低、体验一致、系统级动画 | 无法自定义右侧按钮、标题左对齐、不能放搜索框 | 大多数二级/三级详情页、表单页 |
| **自定义导航栏** | 可完全自定义外观、可放搜索/分段控件 | 需自己适配状态栏、返回按钮、动画 | 首页、列表页等需要搜索或特殊导航的页面 |

**本项目建议**：二级/三级页用原生导航栏；首页、巡检计划、告警等 Tab 根页用自定义导航栏（需要搜索或分段控件）。

#### 胶囊按钮（铁律）

- 胶囊按钮由微信渲染，**无法去除、无法自定义样式**
- 尺寸：87px × 32px，距屏幕右边 10px
- 位置：垂直居中于导航栏内容区（44px 区域内）
- 任何自定义导航栏内容不得覆盖胶囊按钮
- 标题最大宽度：`50vw - 87px - 10px - 7px`（约屏幕中线偏左位置）

#### 状态栏高度

- iPhone X 及以上（刘海/灵动岛）：44px
- iPhone SE/8 及以下：20px
- Android：24-32px 不等
- **必须运行时获取**：`wx.getSystemInfoSync().statusBarHeight`
- 自定义导航栏总高度 = 状态栏高度 + 44px

#### 标题对齐

- 微信原生导航栏：标题左对齐，在返回按钮右侧
- 自定义导航栏：
  - 有返回按钮：标题紧跟返回按钮后（左对齐）
  - 无返回按钮（Tab 根页）：标题可左对齐，或在可用区域内视觉居中
  - **不要强行绝对居中**，右侧胶囊占了约 100px 宽度

#### 自定义导航栏实现要点

```javascript
// app.js 或全局工具函数中获取
const sysInfo = wx.getSystemInfoSync();
const statusBarHeight = sysInfo.statusBarHeight;
const navBarHeight = statusBarHeight + 44;

// 胶囊按钮位置（如需精确对齐）
const capsule = wx.getMenuButtonBoundingClientRect();
const capsuleTop = capsule.top;
const capsuleHeight = capsule.height;
```

```json
// 页面 json 中开启自定义
{
  "navigationStyle": "custom"
}
```

### 底部 TabBar

#### 使用微信原生 TabBar（推荐）

在 `app.json` 中配置：

```json
{
  "tabBar": {
    "color": "#7A7E83",
    "selectedColor": "#007AFF",
    "backgroundColor": "#ffffff",
    "borderStyle": "white",
    "list": [
      { "pagePath": "pages/index/index", "text": "首页",
        "iconPath": "images/tab-home.png", "selectedIconPath": "images/tab-home-on.png" },
      { "pagePath": "pages/plan/plan", "text": "巡检",
        "iconPath": "images/tab-inspection.png", "selectedIconPath": "images/tab-inspection-on.png" },
      { "pagePath": "pages/responsible-sites/responsible-sites", "text": "站点",
        "iconPath": "images/tab-site.png", "selectedIconPath": "images/tab-site-on.png" },
      { "pagePath": "pages/alert/alert", "text": "告警",
        "iconPath": "images/tab-alert.png", "selectedIconPath": "images/tab-alert-on.png" },
      { "pagePath": "pages/mine/mine", "text": "我的",
        "iconPath": "images/tab-mine.png", "selectedIconPath": "images/tab-mine-on.png" }
    ]
  }
}
```

**注意事项**：
- 图标必须是本地 PNG，不支持 SVG；当前项目使用 81×81px 源画布，视觉重量与另外四组图标对齐
- 选中色只能是纯色，不支持渐变
- 角标不在 `app.json` 静态配置；确需未读提示时使用微信运行时 TabBar Badge API，并同步处理清除状态
- 最少 2 个、最多 5 个 Tab
- Tab 页之间切换用 `wx.switchTab`，不能用 `wx.navigateTo`

#### 页面底部留白

TabBar 页面的内容底部必须留出 TabBar 高度 + 安全区，否则内容会被遮挡：

```css
.page-content {
  padding-bottom: calc(50px + env(safe-area-inset-bottom) + 16px);
}
```

### 安全区适配

| 设备 | 顶部状态栏 | 底部安全区 |
|------|-----------|-----------|
| iPhone X/11/12/13/14/15（刘海/灵动岛） | 44px | 34px |
| iPhone SE/8 及以下 | 20px | 0px |
| Android 全面屏 | 24-32px | 0-12px |

**CSS 变量**：使用 `env(safe-area-inset-bottom)` 和 `env(safe-area-inset-top)`

### 其他小程序特有约束

1. **字体**：使用 `PingFang SC` 作为中文字体族，iOS 上自动回退到系统字体
2. **毛玻璃**：小程序不支持 `backdrop-filter`，需降级为半透明纯色背景
3. **阴影**：小程序对 `box-shadow` 支持有限，复杂阴影用图片替代
4. **滚动**：页面滚动用 `onPullDownRefresh` 和 `onReachBottom`，不要用自定义滚动容器
5. **返回**：左上角返回按钮由微信管理，不要自己实现返回手势
6. **页面栈**：最多 10 层页面栈，注意及时用 `redirectTo` 替代 `navigateTo`

### Token 使用方式

```css
/* app.wxss 中全局定义 */
page {
  --color-primary: #007AFF;
  --color-success: #34C759;
  --color-warning: #FF9500;
  --color-error: #FF3B30;
  --color-text-primary: #1d1d1f;
  --color-text-secondary: #3c3c43;
  --color-text-tertiary: #8e8e93;
  --color-background: #f2f2f7;
  --color-card: #ffffff;
  --color-border: rgba(60, 60, 67, 0.12);
  --radius-lg: 12px;
  --radius-xl: 16px;
  --space-4: 8px;
  --space-6: 12px;
  --space-8: 16px;
  --wx-status-bar-height: 44px; /* JS 动态设置 */
  --wx-tab-bar-height: 50px;
}
```

---

## 其他平台适配

### React Web

- Token 可以用现有 CSS 变量或项目既有样式机制
- 优先复用当前 React 组件和依赖；未经批准不新增 UI 依赖
- 本轮正式支持 light；`.dark` 变量仅作为未来草案，不在交付中声称暗色已完成

---

## 组件清单（共 20 个）

| 分类 | 组件 | 优先级 |
|------|------|--------|
| 导航 | Nav Bar 导航栏 | P0 |
| 导航 | Tab Bar 底部标签栏 | P0 |
| 导航 | Segmented Control 分段控件 | P0 |
| 布局 | Action Bar 底部动作栏 | P0 |
| 布局 | Card 分组卡片 | P0 |
| 表单 | Button 按钮 | P0 |
| 表单 | Input 输入框 | P1 |
| 表单 | Switch 开关 | P1 |
| 表单 | Textarea 文本域 | P1 |
| 表单 | Search Bar 搜索栏 | P2 |
| 数据展示 | List Row 列表行 | P0 |
| 数据展示 | Status Chip 状态标签 | P0 |
| 数据展示 | Quick Actions 快捷入口 | P1 |
| 数据展示 | Progress 进度指示器 | P1 |
| 数据展示 | Photo Grid 照片九宫格 | P1 |
| 数据展示 | Timeline 时间线 | P1 |
| 数据展示 | Badge 角标 | P2 |
| 反馈 | Bottom Sheet 底部面板 | P0 |
| 反馈 | Empty State 空状态 | P1 |
| 反馈 | Action Sheet 操作表 | P2 |

### 组件使用边界

每个基础组件的 JSON 规格都包含 `whenToUse` 与 `whenNotToUse`。新增页面先选合适的既有组件，再按页面职责组合；不为单一页面新增组件或平行交互。

| 组件 | 什么时候用 | 什么时候不用 |
|------|------------|--------------|
| Nav Bar | 二级/三级页需要返回、标题和轻量右侧动作 | 不叠加多个导航栏或替代内容标题 |
| Tab Bar | 五个稳定根任务之间切换 | 不放在现场子流程、表单提交或任务面板 |
| Segmented Control | 少量互斥视图即时切换 | 不用于层级导航或延迟提交设置 |
| Button | 执行明确动作，页面只有一个主动作 | 不把静态状态或筛选标签做成按钮 |
| Input | 输入一行短文本或单值字段 | 不用于长段说明、开关或多选 |
| Switch | 立即切换可逆的开/关设置 | 不用于互斥多选或提交动作 |
| Textarea | 输入可换行的描述、原因或备注 | 不用于单行字段或结构化多项表单 |
| Search Bar | 列表按关键词快速缩小 | 不在少量固定选项或详情页替代筛选 |
| Card | 分组同一信息或动作并建立层级 | 不嵌套卡片或装饰整页 |
| List Row | 同层级短信息、设置项或详情入口 | 不承载长正文或多个主动作 |
| Status Chip | 紧凑表达一个当前状态 | 不用颜色区分类型或承担主操作 |
| Photo Grid | 浏览、添加、删除同一对象的照片证据 | 不承载非照片附件或装饰图片 |
| Timeline | 按时间解释已发生动作和当前节点 | 不作流程导航或百分比进度 |
| Badge | 提示未读数量或少量计数 | 不表达完整状态或长文案 |
| Empty State | 确实无内容且需要给出下一动作 | 不把加载、失败或无权伪装为空 |
| Action Sheet | 从短选项中做一次选择 | 不承载长表单或替代页面导航 |

---

## 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| 1.2.1 | 2026-09-22 | 接入长期协作矩阵；修正五 Tab 目标、React Web 范围、图标和角标事实，并区分设计静态交付与开发接线交付 |
| 1.2.0 | 2026-09-01 | 新增底部动作栏、底部面板、快捷入口、进度指示器 4 个组件；新增 40 个 SVG 图标库；补强按钮、状态标签、分段控件、空状态、操作表规范 |
| 1.1.0 | 2026-08-22 | 新增微信小程序专属适配（导航栏/胶囊按钮/TabBar/安全区），UI Kit 新增小程序专区 |
| 1.0.0 | 2026-08-22 | 初始版本，16 个核心组件 + Token 体系 |

---

## 维护说明

- 新增组件：在 `components/` 下添加 JSON 规格文件，更新 `components/index.json`
- 修改 Token：修改 `colors_and_type.css`，然后重新生成 `css.json`
- 新增 Token 必须遵循"先加色板、再映射语义"的原则
- 组件变体不要随意增加，每个变体都要有明确的使用场景
