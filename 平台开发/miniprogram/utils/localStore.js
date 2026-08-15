// 本地实体存储：巡检闭环（打卡/拍照/提交）的"本地先落库"层。
// 与 utils/request.js 的"请求级失败队列"互补——这里存的是业务实体（带本地照片路径），
// 确保断网时也能走完「打卡→拍照→提交」闭环，且 App 重启后实体仍在，联网后静默同步。
const KEY = 'local_insp_ops';
const { isPendingInspectionSubmit } = require('./inspectionSubmissionState.js');
const { getUser } = require('./auth.js');

function currentOwnerUserId() {
  const user = getUser() || {};
  return user.id == null ? '' : String(user.id);
}

function readAll() {
  try { return wx.getStorageSync(KEY) || []; } catch (e) { return []; }
}

function belongsToOwner(operation, ownerUserId) {
  return !!ownerUserId && operation && operation.ownerUserId != null
    && String(operation.ownerUserId) === String(ownerUserId);
}

function read(ownerUserId) {
  const owner = ownerUserId == null ? currentOwnerUserId() : String(ownerUserId);
  return readAll().filter((operation) => belongsToOwner(operation, owner));
}

function write(list) {
  try { wx.setStorageSync(KEY, list); } catch (e) {}
}

// type: 'checkin' | 'submit'
// data: 业务载荷（submit 内置 localPhotos 本地路径数组、siteId）
function addOp(type, data) {
  const ownerUserId = currentOwnerUserId();
  if (!ownerUserId) return null;
  const list = readAll();
  if (type === 'checkin') {
    const existing = list.find((operation) => operation.type === 'checkin'
      && belongsToOwner(operation, ownerUserId)
      && operation.syncStatus === 'pending'
      && String(operation.data.site_id) === String(data.site_id));
    if (existing) {
      data._idempotency_key = existing.id;
      return existing.id;
    }
  }
  if (type === 'submit') {
    const existing = list.find((operation) => belongsToOwner(operation, ownerUserId)
      && isPendingInspectionSubmit([operation], data.item_id, data.plan_id));
    if (existing) {
      data._idempotency_key = existing.id;
      return existing.id;
    }
  }
  const op = {
    id: 'op_' + Date.now() + '_' + Math.floor(Math.random() * 1e4),
    type: type,
    data: data,
    ownerUserId,
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

function getPending(ownerUserId) {
  return read(ownerUserId).filter((o) => o.syncStatus === 'pending');
}

function markSynced(id, ownerUserId) {
  const owner = ownerUserId == null ? currentOwnerUserId() : String(ownerUserId);
  const list = readAll();
  const o = list.find((x) => x.id === id && belongsToOwner(x, owner));
  if (o) { o.syncStatus = 'synced'; write(list); }
}

function markRejected(id, error, ownerUserId) {
  const owner = ownerUserId == null ? currentOwnerUserId() : String(ownerUserId);
  const list = readAll();
  const o = list.find((x) => x.id === id && belongsToOwner(x, owner));
  if (o) {
    o.syncStatus = 'rejected';
    o.syncError = error || '服务器拒绝了该操作';
    o.rejectedAt = Date.now();
    write(list);
  }
}

function removeOp(id, ownerUserId) {
  const owner = ownerUserId == null ? currentOwnerUserId() : String(ownerUserId);
  write(readAll().filter((x) => x.id !== id || !belongsToOwner(x, owner)));
}

function queueCount() {
  return getPending().length;
}

// 取某站点尚未同步的本地打卡，供闭环状态判断
function getLocalCheckIn(siteId) {
  return read().find((o) => o.type === 'checkin'
    && String(o.data.site_id) === String(siteId) && o.syncStatus === 'pending') || null;
}

function getSiteCheckIn(siteId) {
  return read()
    .filter((o) => o.type === 'checkin'
      && String(o.data.site_id) === String(siteId)
      && o.syncStatus === 'pending')
    .sort((a, b) => b.createdAt - a.createdAt)[0] || null;
}

// 检查项未同步提交是现场端的事实源：同一项同步完成前不可再次提交。
function getPendingSubmit(itemId, planId) {
  return getPending().find((op) => isPendingInspectionSubmit([op], itemId, planId)) || null;
}

function getRejectedSubmit(itemId, planId) {
  return read().find((operation) => operation.type === 'submit'
    && operation.syncStatus === 'rejected'
    && String(operation.data.item_id) === String(itemId)
    && String(operation.data.plan_id) === String(planId)) || null;
}

function clearRejectedSubmit(itemId, planId) {
  const owner = currentOwnerUserId();
  const list = readAll();
  const removed = list.filter((operation) => operation.type === 'submit'
    && belongsToOwner(operation, owner)
    && operation.syncStatus === 'rejected'
    && String(operation.data.item_id) === String(itemId)
    && String(operation.data.plan_id) === String(planId));
  if (removed.length) write(list.filter(operation => !removed.includes(operation)));
  return removed;
}

module.exports = {
  addOp, getPending, markSynced, markRejected, removeOp, queueCount,
  getLocalCheckIn, getSiteCheckIn, getPendingSubmit, getRejectedSubmit, clearRejectedSubmit,
  read, readAll, write, currentOwnerUserId, KEY
};
