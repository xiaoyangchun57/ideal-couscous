// 后端基础地址（HTTPS + 已备案域名）
// 开发阶段：在微信开发者工具「详情 → 本地设置」勾选「不校验合法域名」
// 生产阶段：改为真实 HTTPS 域名，并在小程序后台配置 request 合法域名
//
// 当前为开发联调地址（局域网 Flask，app.run host=0.0.0.0:5000）：
//   - 开发者工具模拟器 / 真机预览 均可访问
//   - 需在开发者工具「本地设置」勾选「不校验合法域名、TLS、HTTPS 证书」
//   - 若接口无响应，请先确认 backend/app.py 已启动
const CONFIG = {
  BASE_URL: 'http://192.168.2.103:5000'
};

module.exports = CONFIG;
