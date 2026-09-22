# 开发接线规范

> 设计 → 代码 → 后端对接的统一标准。
> 各执行单元端到端负责自己的模块，但字段和状态必须服从现有业务契约。

本文件规定展示映射和交付格式，不授权设计或前端单方面修改后端字段、权限、状态迁移或公共接口。示例中的字段、状态和端点仅用于说明格式，实际实现以当前 `CURRENT` 文件、既有接口和后端契约为准。

---

## 1. Token 使用纪律

### 只用语义 Token，不用原始色板
```css
/* ✅ 正确 */
background: var(--color-primary);
color: var(--color-text-secondary);

/* ❌ 错误 */
background: #007AFF;
color: var(--palette-gray-700);
```

### 页面级 WXSS 不定义新颜色
- 需要新的颜色？先确认是不是真的没有对应语义色
- 确实需要新增 → 走 `CONTRIBUTING.md` 的"新增 Token 流程"
- 不允许在页面 WXSS 顶部写 `--my-custom-color: xxx`

### 间距统一用 space 系列
```css
/* ✅ 正确 */
margin-bottom: var(--space-4);
padding: var(--space-3) var(--space-4);

/* ❌ 错误 */
margin-bottom: 13px;   /* 不是 2 的倍数 */
padding: 7px 15px;     /* 不标准 */
```

间距必须是 2px 的整数倍，优先用 `--space-1` 到 `--space-20`。

---

## 2. 状态枚举规范

### 命名规则
- 状态值（后端字段）：小写蛇形 `snake_case`，如 `pending_review`
- 显示文案（前端展示）：中文，面向用户
- 语义色：必须映射到设计系统 5 个语义色之一

### 状态 → 颜色映射表（接线时必填）

每个有状态的列表/详情，都必须有这张表：

| 状态值 (status) | 显示文案 | 语义色 | 对应按钮/操作 |
|----------------|---------|--------|-------------|
| `pending` | 待处理 | warning（橙） | 去处理 |
| `processing` | 进行中 | info（蓝） | 查看详情 |
| `completed` | 已完成 | success（绿） | 查看结果 |
| `rejected` | 已驳回 | error（红） | 重新提交 |
| `cancelled` | 已取消 | default（灰） | — |

### 常见展示语义的推荐映射（不要自行改后端状态）

| 场景 | 状态值 | 文案 | 颜色 |
|------|--------|------|------|
| 等待审核/审批 | `pending_review` | 待审核 | 🟠 warning |
| 等待补拍/补充 | `pending_supplement` | 待补拍 | 🟠 warning |
| 等待整改 | `pending_rectify` | 待整改 | 🟠 warning |
| 已通过/已批准 | `approved` | 已通过 | 🟢 success |
| 已驳回/已拒绝 | `rejected` | 已驳回 | 🔴 error |
| 进行中/执行中 | `in_progress` | 进行中 | 🔵 info |
| 已完成/已结束 | `completed` | 已完成 | 🟢 success |
| 已取消/已撤销 | `cancelled` | 已取消 | ⚪ default |
| 异常/失败 | `failed` | 异常 | 🔴 error |
| 提示级告警 | `alert_notice` | 提示 | 🟡 notice |

> 新接口应避免为同一业务状态创造多个名称；既有接口存在不同枚举时，应在前端展示映射层收口。未经产品和后端契约 Review，不得仅为视觉一致性重命名公共字段或迁移存量状态。

---

## 3. 字段 → 组件位置映射

### List Row 标准映射

| 位置 | 字段示例 | 样式 |
|------|---------|------|
| 左侧图标 | `type_icon` | 18px, `--color-icon-secondary` |
| 主标题 | `name` / `title` | 15px, `--color-text-primary` |
| 副标题 | `subtitle` / `address` | 12px, `--color-text-tertiary` |
| 右侧状态 | `status` | Status Chip |
| 右侧箭头 | 固定 | Chevron Right, `--palette-gray-400` |

### Bottom Sheet 表单字段接线

每个 Sheet 交付时，必须列出：

| 字段名 | 类型 | 必填 | 校验规则 | 占位符 | 错误提示 |
|--------|------|------|---------|--------|---------|
| `reagent_name` | string | 是 | 非空，最多 50 字 | 请输入试剂名称 | 请输入试剂名称 |
| `replace_reason` | string | 是 | 从枚举中选 | 请选择更换原因 | 请选择更换原因 |
| `batch_no` | string | 否 | 最多 20 字 | 请输入批号（选填） | — |

### 提交接口接线示例

以下仅说明交付时应记录哪些信息，不是本项目已存在的接口契约：

```
接口：POST /api/reagent/replace
请求参数：{ site_id, reagent_id, reason, batch_no, photos[] }
成功返回：{ id, status }
失败返回：{ code, message }
成功后动作：关闭 Sheet + Toast "提交成功" + 刷新列表
失败后动作：保留输入 + 字段下方显示错误 / Toast 错误信息
```

---

## 4. 交互状态接线

### Bottom Sheet / 表单的 5 种状态

每个表单型 Sheet 必须覆盖这 5 种状态：

| 状态 | 表现 | 能否关闭 |
|------|------|---------|
| 默认态 | 空白表单，提交按钮可点 | ✅ 能 |
| 填写中 | 有输入内容，实时校验 | ✅ 能 |
| 提交中 | 按钮 Loading + 禁用，蒙层不可点关闭 | ❌ 不能 |
| 失败态 | 按钮恢复，错误提示显示在字段下方，**保留用户输入** | ✅ 能 |
| 成功态 | 成功反馈（Toast 或成功页），自动关闭 | — |

### 列表的 4 种状态

| 状态 | 组件 |
|------|------|
| 加载中 | 骨架屏 / Loading |
| 有数据 | 正常列表 |
| 空数据 | Empty State (no-data) |
| 加载失败 | Empty State (error) + 重试按钮 |

---

## 5. 代码组织规范

### 页面目录结构
```
pages/responsible-sites/
├── responsible-sites.wxml     # 页面结构
├── responsible-sites.wxss     # 页面样式（只用 Token）
├── responsible-sites.js       # 页面逻辑
├── responsible-sites.json     # 页面配置
└── components/                # 页面级子组件（可选）
    └── site-card/
```

### 样式文件规则
- 页面级 WXSS 只写本页面特有的布局样式
- 通用组件样式放到设计系统的 `components.css`
- 不写超过 3 层的嵌套选择器
- 不使用 `!important`（除非是覆盖第三方组件）

### 命名规则
- 页面/组件类名：`kebab-case`，如 `site-card`、`reagent-item`
- 状态修饰类：`--` 后缀，如 `site-card--active`、`button--loading`
- JS 变量/函数：`camelCase`
- 后端接口字段：`snake_case`（后端定义的不动）

---

## 6. 跟后端对接时的设计介入点

设计需要核对字段在界面上的表达，产品和开发负责业务契约：

1. **状态枚举对齐**：后端返回的状态值，前端必须能映射到既有语义色。映射不了时，先由产品确认业务语义，再由开发决定扩展展示映射还是变更接口；设计评审不能单独改后端。
2. **字段含义澄清**：同一个字段在不同接口里含义不一样？必须拉齐，不能前端各自适配。
3. **边界情况确认**：接口返回 null / 空数组 / 缺字段时怎么展示？要提前说好，不要上线后才发现白屏。
4. **分页/加载逻辑**：列表页的分页方式、下拉刷新、上拉加载更多，前后端统一口径。
