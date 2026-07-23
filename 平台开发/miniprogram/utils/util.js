function pad(n) { return n < 10 ? '0' + n : '' + n; }

// 后端时间格式：'YYYY-MM-DD HH:MM:SS' 或 ISO；统一裁剪成 'MM-DD HH:MM'
function fmtTime(s) {
  if (!s) return '';
  let str = s.replace('T', ' ').replace('Z', '');
  if (str.length >= 16) return str.slice(5, 16);
  if (str.length >= 10) return str.slice(5);
  return str;
}

function nowStr() {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function todayStr() {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// 相对时间（用于通知）
function relativeTime(s) {
  if (!s) return '';
  const t = new Date((s || '').replace(/-/g, '/')).getTime();
  if (isNaN(t)) return fmtTime(s);
  const diff = Date.now() - t;
  const min = Math.floor(diff / 60000);
  if (min < 1) return '刚刚';
  if (min < 60) return min + ' 分钟前';
  const h = Math.floor(min / 60);
  if (h < 24) return h + ' 小时前';
  const d = Math.floor(h / 24);
  if (d < 30) return d + ' 天前';
  return fmtTime(s);
}

module.exports = { pad, fmtTime, nowStr, todayStr, relativeTime };
