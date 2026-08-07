// 后端基础地址（HTTPS + 已备案域名）
// 开发阶段：在微信开发者工具「详情 → 本地设置」勾选「不校验合法域名」
// 生产阶段：改为真实 HTTPS 域名，并在小程序后台配置 request 合法域名
//
// 默认让模拟器、真机预览和已发布版本访问同一线上后端，避免双端数据和功能分叉。
// 本地联调时显式改为 true；真机不能访问 127.0.0.1，应改成电脑在同一局域网的地址。
const USE_LOCAL_API = false;
const LOCAL_API_BASE_URL = 'http://127.0.0.1:5000';

const CONFIG = {
  BASE_URL: USE_LOCAL_API ? LOCAL_API_BASE_URL : 'https://ops.hhyc-tec.cn'
};

module.exports = CONFIG;
