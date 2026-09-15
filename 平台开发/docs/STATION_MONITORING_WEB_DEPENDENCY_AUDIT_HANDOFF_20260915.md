# 站点监测 Web 依赖审计交接

> 交接日期：2026-09-15
>
> 审计对象：`平台开发/react-vite/package-lock.json`
>
> 基线分支：`codex/station-monitoring-web-db20184`
>
> Web 实现提交：`dac5da0b5033c1c96d87f20d35fde4574e53db79`
>
> 当前状态：`DEPENDENCY_REVIEW_REQUIRED`
>
> 本文件只记录审计结果；未升级依赖、未运行 `npm audit fix`、未部署或操作生产环境。

## 1. 审计结论

执行 `npm audit --json` 后，npm 报告：

- `moderate`：1 个受影响包；
- `high`：7 个受影响包；
- `critical`：0；
- 合计：8 个受影响包。

npm 的 8 项是按受影响包节点计数，不是按公告计数。8 个包共关联 12 条底层安全公告；`react-router-dom` 因依赖存在漏洞的 `react-router` 被另计为一个受影响包，本身没有额外公告。

## 2. 受影响依赖

| 包及当前版本 | 级别 | 问题与公告 | npm 建议的安全范围 |
| --- | --- | --- | --- |
| `baseline-browser-mapping@2.10.40` | Moderate | 非法输入可终止进程，造成拒绝服务。[GHSA-w5vr-8v7q-w6rv](https://github.com/advisories/GHSA-w5vr-8v7q-w6rv) | `>=2.11.0` |
| `brace-expansion@1.1.16` | High | 无界展开或中间数组可能导致内存耗尽。[GHSA-mh99-v99m-4gvg](https://github.com/advisories/GHSA-mh99-v99m-4gvg)、[GHSA-rgw5-rvv9-x895](https://github.com/advisories/GHSA-rgw5-rvv9-x895) | `>=1.1.18` |
| `browserslist@4.28.4` | High | 无界查询缓存可导致 OOM；恶意自定义统计文件可造成崩溃或原型属性写入。[GHSA-c83g-rgw3-j3cx](https://github.com/advisories/GHSA-c83g-rgw3-j3cx)、[GHSA-73wf-gq98-2v4g](https://github.com/advisories/GHSA-73wf-gq98-2v4g) | `>=4.28.7` |
| `js-yaml@4.3.0` | High | 特制 `!!omap` 或 merge key 可造成高 CPU 消耗。[GHSA-5p4m-2wfm-xmqj](https://github.com/advisories/GHSA-5p4m-2wfm-xmqj)、[GHSA-2883-xcg3-v3hh](https://github.com/advisories/GHSA-2883-xcg3-v3hh) | `>=4.3.2` |
| `nanoid@3.3.15` | High | 非安全或自定义生成器在异常 size 下可能无限循环。[GHSA-28wg-ghj8-5hjv](https://github.com/advisories/GHSA-28wg-ghj8-5hjv)、[GHSA-2v37-7h3g-55p8](https://github.com/advisories/GHSA-2v37-7h3g-55p8) | `>=3.3.18` |
| `postcss@8.5.15` | High | 恶意 `sourceMappingURL` 可触发路径穿越并读取本地 `.map` 文件。[GHSA-r28c-9q8g-f849](https://github.com/advisories/GHSA-r28c-9q8g-f849)、[GHSA-fxqj-rqcc-2cmp](https://github.com/advisories/GHSA-fxqj-rqcc-2cmp) | `>8.5.22` |
| `react-router@7.18.0` | High | RSC 模式下可能绕过 CSRF 检查，在返回 400 前执行 Action。[GHSA-qwww-vcr4-c8h2](https://github.com/advisories/GHSA-qwww-vcr4-c8h2) | `>=7.18.2` |
| `react-router-dom@7.18.0` | High | 直接依赖上述受影响的 `react-router`，因此被 npm 单独计入 | `>=7.18.2` |

## 3. 当前依赖路径

```text
react-vite
├─ eslint-plugin-react-hooks@7.1.1
│  └─ @babel/core@7.29.7
│     └─ @babel/helper-compilation-targets@7.29.7
│        └─ browserslist@4.28.4
│           └─ baseline-browser-mapping@2.10.40
├─ eslint-plugin-react@7.37.5
│  └─ minimatch@3.1.5
│     └─ brace-expansion@1.1.16
├─ eslint@9.39.4
│  └─ @eslint/eslintrc@3.3.5
│     └─ js-yaml@4.3.0
├─ react-router-dom@7.18.0
│  └─ react-router@7.18.0
└─ vite@8.1.0
   └─ postcss@8.5.15
      └─ nanoid@3.3.15
```

其中 `react-router-dom` 是项目直接依赖；其余均为经 ESLint、Babel 或 Vite 引入的间接依赖。

## 4. 暴露面判断

1. `react-router` 公告针对 RSC Action。当前 Web 使用 Vite、`BrowserRouter` 和浏览器端 SPA 路由，尚无 RSC 使用证据，因此该公告在当前部署形态下是否可利用尚未成立；直接依赖版本仍应升级到安全范围。
2. `postcss` 和 `nanoid` 位于构建链。若构建过程只处理仓库内受信任源码，生产静态页面不会直接暴露其解析入口；CI 或本地构建若处理外部提交的 CSS/source map，仍需防范。
3. `baseline-browser-mapping`、`brace-expansion`、`browserslist` 和 `js-yaml` 位于 lint/Babel 工具链，主要风险是构建或检查阶段处理恶意输入时出现资源耗尽、崩溃或异常属性写入。
4. 上述判断只描述当前可见调用方式，不等于漏洞豁免。依赖版本仍处于公告受影响范围，冻结候选前应完成受控升级和回归。

## 5. 建议修复顺序

1. 优先升级直接依赖 `react-router-dom` 和 `react-router` 至 `>=7.18.2`，验证登录回跳、受保护路由、嵌套路由、站点详情和接入观察权限。
2. 通过 Vite 支持的依赖范围更新 `postcss` 至 `>8.5.22`、`nanoid` 至 `>=3.3.18`；不要未经论证添加长期 `overrides`。
3. 更新 ESLint/Babel 相关父依赖或锁文件解析结果，使 `brace-expansion`、`browserslist`、`baseline-browser-mapping` 和 `js-yaml` 进入安全范围。
4. 重新运行审计，要求 `high=0`、`critical=0`；若仍保留风险，必须记录无法升级原因、实际不可利用证据和复查期限。

不建议直接在当前 Web 功能提交上运行无审查的 `npm audit fix`。依赖升级应形成独立提交，便于隔离功能差异、审查锁文件变化和回退。

## 6. 升级后验证

至少执行：

```text
npm ci
npm audit --json
npm run test:api
node --test src/pages/sites/stationMonitoring.test.js src/pages/sites/stationMonitoringWiring.test.js
npm run lint
npm run build
```

还需在隔离环境完成真实浏览器验证：

- admin、reviewer、operator 的路由可见性与拒绝行为；
- `/sites`、`/sites/:siteId`、`/sites/data-access`；
- 登录失效后的回跳；
- 驾驶舱、告警和全局搜索入口；
- 桌面端与移动端刷新失败、保留旧结果和重试。

## 7. 本次审计证据

- `npm audit --json`：命令按 npm 约定以退出码 1 返回，报告 1 moderate、7 high、0 critical。
- `npm ls baseline-browser-mapping brace-expansion browserslist js-yaml nanoid postcss react-router react-router-dom --all`：成功，依赖路径见第 3 节。
- 未执行 `npm audit fix`。
- 未修改 `package.json` 或 `package-lock.json`。
- 审计执行阶段未提交或推送依赖变更；本交接文档可单独提交至协作分支，不代表漏洞已修复。
- 未合并、打标签、部署或操作生产数据。

## 8. 交付判断

当前站点监测 Web 功能提交的测试与构建已通过，但依赖审计仍存在高危报告，不能仅依据功能测试宣称依赖风险已闭环。下一断点是建立独立依赖升级分支，完成受控升级、锁文件审查、自动化回归和真实 Web UI 验收后，再交产品与代码 Review。
