# 小程序项目入口

微信开发者工具只导入本目录的上一级“平台开发”目录。上一级 `project.config.json` 通过
`miniprogramRoot: "miniprogram/"` 指向本目录，是开发、真机调试、预览和上传的唯一入口。

`project.private.config.json` 是本机私有设置，不参与项目入口判断。

API 档位只由小程序 `envVersion` 决定：

- `develop`（开发者工具模拟器和真机调试）固定访问 `http://192.168.2.105:5000`，档位为 `local`。
- `trial`、`release` 固定访问 `https://ops.hhyc-tec.cn`，档位为 `online`。

开发 API 不可达时应直接显示网络失败，不使用存储覆盖、过期切换或线上回退。真实 UI 证据格式见
上一级 [`docs/UI_VALIDATION_WORKFLOW.md`](../docs/UI_VALIDATION_WORKFLOW.md)。
