// 统一请求层：Bearer 鉴权 + 超时 + 指数退避重试 + 弱网失败队列
const { getToken, clear } = require('./auth.js');
const CONFIG = require('./config.js');

const FAIL_QUEUE_KEY = 'fail_queue';
let flushing = false; // 失败队列重传锁（防网络恢复与手动同步并发重入）

function buildUrl(path) {
  return CONFIG.BASE_URL + path;
}

function getQueue() {
  try { return wx.getStorageSync(FAIL_QUEUE_KEY) || []; } catch (e) { return []; }
}
function saveQueue(q) {
  try { wx.setStorageSync(FAIL_QUEUE_KEY, q); } catch (e) {}
}
function queueCount() {
  return getQueue().length;
}

// 恢复网络后重传失败队列（写类请求）
// 复用主请求的鉴权/重试/401 跳转逻辑；成功即丢弃，失败按状态码决定保留与否
function flushQueue(onResolve) {
  if (flushing) return;
  const q = getQueue();
  if (!q.length) return;
  flushing = true;
  const remain = [];
  let pending = q.length;
  q.forEach((task) => {
    request(task.url, task.method, task.data, { retry: 2, queue: false })
      .then((resp) => { if (onResolve) onResolve(task, resp); })
      .catch((err) => {
        const st = (err && err.status) || 0;
        const code = (err && err.code) || 0;
        // 5xx 或网络错误（-1）保留待下次重传；4xx（含 401 已由 request 跳登录）丢弃
        if (st >= 500 || code === -1) remain.push(task);
      })
      .finally(() => {
        pending -= 1;
        if (pending === 0) {
          flushing = false;
          saveQueue(remain);
        }
      });
  });
}

// 主请求
function request(path, method, data, options) {
  options = options || {};
  const maxRetry = options.retry != null ? options.retry : 2;
  const timeout = options.timeout || 12000;
  const authHeader = getToken() ? { 'Authorization': 'Bearer ' + getToken() } : {};

  return new Promise((resolve, reject) => {
    function attempt(n) {
      wx.request({
        url: buildUrl(path),
        method: method,
        data: data,
        timeout: timeout,
        header: Object.assign({ 'Content-Type': 'application/json' }, authHeader),
        success(res) {
          if (res.statusCode === 200 || res.statusCode === 201) {
            resolve(res.data);
          } else if (res.statusCode >= 500 && n < maxRetry) {
            setTimeout(() => attempt(n + 1), Math.min(1000 * Math.pow(2, n), 8000));
          } else if (res.statusCode === 401) {
            // 令牌失效：清理并跳登录
            clear();
            wx.reLaunch({ url: '/pages/login/login' });
            reject(res.data || { error: '登录已失效' });
          } else {
            reject(res.data || { error: '请求失败', status: res.statusCode });
          }
        },
        fail(err) {
          if (n < maxRetry) {
            setTimeout(() => attempt(n + 1), Math.min(1000 * Math.pow(2, n), 8000));
          } else {
            // 写类请求进入失败队列，待网络恢复自动重传
            if (options.queue !== false && (method === 'POST' || method === 'PUT' || method === 'DELETE')) {
              const q = getQueue();
              // 幂等去重：同一写请求（同 url+方法+数据）已在队列则不重复入队，防弱网重复提交
              const sig = method + ':' + path + ':' + JSON.stringify(data || null);
              const exists = q.some(t => (t.method + ':' + t.url + ':' + JSON.stringify(t.data || null)) === sig);
              if (!exists) {
                q.push({ url: path, method: method, data: data, ts: Date.now() });
                saveQueue(q);
              }
            }
            reject(err);
          }
        }
      });
    }
    attempt(0);
  });
}

module.exports = { request, flushQueue, getQueue, saveQueue, queueCount };
