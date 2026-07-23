// 本地实体存储：巡检闭环（打卡/拍照/提交）的"本地先落库"层。
// 与 utils/request.js 的"请求级失败队列"互补——这里存的是业务实体（带本地照片路径），
// 确保断网时也能走完「打卡→拍照→提交」闭环，且 App 重启后实体仍在，联网后静默同步。
const KEY = 'local_insp_ops';

function read() {
  try { return wx.getStorageSync(KEY) || []; } catch (e) { return []; }
}
function write(list) {
  try { wx.setStorageSync(KEY, list); } catch (e) {}
}

// type: 'checkin' | 'submit'
// data: 业务载荷（submit 内置 localPhotos 本地路径数组、siteId）
function addOp(type, data) {
  const list = read();
  const op = {
    id: 'op_' + Date.now() + '_' + Math.floor(Math.random() * 1e4),
    type: type,
    data: data,
    syncStatus: 'pending',
    createdAt: Date.now(),
  };
  op.data = Object.assign({}, data, { _idempotency_key: op.id });
  // 调用方紧接着发起在线请求，复用同一幂等键。
  data._idempotency_key = op.id;
  list.push(op);
  write(list);
  return op.id;
}

function getPending() {
  return read().filter((o) => o.syncStatus === 'pending');
}

function markSynced(id) {
  const list = read();
  const o = list.find((x) => x.id === id);
  if (o) { o.syncStatus = 'synced'; write(list); }
}

function removeOp(id) {
  write(read().filter((x) => x.id !== id));
}

function queueCount() {
  return getPending().length;
}

// 取某站点尚未同步的本地打卡，供闭环状态判断
function getLocalCheckIn(siteId) {
  return read().find((o) => o.type === 'checkin' && o.data.site_id === siteId && o.syncStatus === 'pending') || null;
}

function getSiteCheckIn(siteId) {
  return read()
    .filter((o) => o.type === 'checkin' && o.data.site_id === siteId)
    .sort((a, b) => b.createdAt - a.createdAt)[0] || null;
}

module.exports = { addOp, getPending, markSynced, removeOp, queueCount, getLocalCheckIn, getSiteCheckIn, read, write, KEY };
