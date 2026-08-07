// 统一请求层：Bearer 鉴权 + 超时 + 指数退避重试 + 弱网失败队列
const { getToken, clear } = require('./auth.js');
const CONFIG = require('./config.js');

const FAIL_QUEUE_KEY = 'fail_queue';
let flushing = null; // 失败队列重传 Promise（防网络恢复与手动同步并发重入）

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

function taskSignature(task) {
  return (task.method || '') + ':' + (task.url || '') + ':' + JSON.stringify(task.data || null);
}

// 恢复网络后重传失败队列（写类请求）
// 复用主请求的鉴权/重试/401 跳转逻辑；成功即丢弃，失败按状态码决定保留与否
function flushQueue(onResolve) {
  if (flushing) return flushing;
  const q = getQueue();
  if (!q.length) return Promise.resolve({ synced: 0, remaining: 0, rejected: [] });
  const remain = [];
  const rejected = [];
  const jobs = q.map((task) => request(task.url, task.method, task.data, { retry: 2, queue: false })
    .then((resp) => {
      if (onResolve) onResolve(task, resp);
      return { synced: true };
    })
    .catch((err) => {
      const st = (err && err.status) || 0;
      const code = (err && err.code) || 0;
      // 5xx 或网络错误保留；业务 4xx 记录原因并移出队列，避免永远显示“待同步”。
      if (st >= 500 || code === -1) {
        remain.push(task);
        return { pending: true };
      }
      rejected.push({ task, error: err || { error: '请求被服务器拒绝' } });
      return { rejected: true };
    }));
  flushing = Promise.all(jobs).then((results) => {
    // Keep requests added while this snapshot was replaying; otherwise a weak-network
    // operation created during the flush would be overwritten by the old snapshot.
    const original = new Set(q.map(taskSignature));
    const additions = getQueue().filter(task => !original.has(taskSignature(task)));
    saveQueue(remain.concat(additions));
    return {
      synced: results.filter(item => item.synced).length,
      remaining: remain.length + additions.length,
      rejected,
    };
  }).finally(() => { flushing = null; });
  return flushing;
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
            reject(Object.assign({}, res.data || { error: '登录已失效' }, { status: 401 }));
          } else {
            const body = (res.data && typeof res.data === 'object') ? res.data : { error: '请求失败' };
            reject(Object.assign({}, body, { status: res.statusCode }));
          }
        },
        fail(err) {
          err = Object.assign({}, err || {}, { code: -1, status: 0, network: true });
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
              // 让调用页区分「业务失败」与「已安全落入离线队列」。
              // 仍 reject，避免页面将尚未同步的操作误呈现为已完成。
              err = Object.assign(err || {}, { queued: true });
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
