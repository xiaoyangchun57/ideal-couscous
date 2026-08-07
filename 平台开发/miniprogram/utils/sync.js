// 本地实体同步引擎：联网后把本地巡检闭环实体（打卡/提交）静默回放至服务端。
// 每张离线照片：先以本地路径上传取回 URL，再并入提交载荷，保证"带照片走完闭环"。
const localStore = require('./localStore.js');
const { fileToBase64 } = require('./photos.js');
const api = require('../services/api.js');
let localFlushPromise = null;

async function flushLocalOpsInternal() {
  const pending = localStore.getPending();
  const summary = { synced: 0, remaining: 0, rejected: [] };
  if (!pending.length) return summary;
  // 按创建时间顺序回放，保证闭环完整（先打卡/照片，后提交）
  const ordered = pending.slice().sort((a, b) => a.createdAt - b.createdAt);
  for (const op of ordered) {
    try {
      if (op.type === 'submit') {
        const payload = JSON.parse(JSON.stringify(op.data));
        // 离线照片：本地路径 → 上传取 URL → 并入 photo_urls
        if (Array.isArray(payload.localPhotos) && payload.localPhotos.length) {
          const urls = [];
          const photoMeta = Array.isArray(payload.localPhotoMeta) ? payload.localPhotoMeta : [];
          for (let index = 0; index < payload.localPhotos.length; index += 1) {
            const p = payload.localPhotos[index];
            try {
              const b64 = await fileToBase64(p);
              const r = await api.uploadSitePhoto(
                payload.siteId, b64, op.id + ':photo:' + index, photoMeta[index] || {}
              );
              const u = r && r.url;
              if (u) urls.push(u);
            } catch (e) {
              // Keep transient failures retryable, but reject permanently invalid photos.
              if (e && (e.status >= 400 || e.code === -1)) throw e;
              throw Object.assign(new Error('照片文件不可读，请重新选择或拍摄'), { status: 422 });
            }
          }
          if (urls.length !== payload.localPhotos.length) {
            throw new Error('仍有照片未上传，保留作业等待下次同步');
          }
          let existing = [];
          try { existing = JSON.parse(payload.photo_urls || '[]'); } catch (e) { existing = []; }
          payload.photo_urls = JSON.stringify(existing.concat(urls.filter(Boolean)));
          delete payload.localPhotos;
          delete payload.localPhotoMeta;
        }
        await api.submitItem(payload);
        (op.data.localPhotos || []).forEach((filePath) => {
          wx.removeSavedFile({ filePath, fail() {} });
        });
        localStore.markSynced(op.id);
        summary.synced += 1;
        api.trackEvent('inspection.item.synced', { site_id: op.data.siteId, item_id: op.data.item_id, operation_id: op.id, offline: true });
      } else if (op.type === 'checkin') {
        await api.checkIn(op.data, true);
        localStore.markSynced(op.id);
        summary.synced += 1;
        api.trackEvent('inspection.checkin.synced', { site_id: op.data.site_id, operation_id: op.id, offline: true });
      }
      // 兼容：若未来有独立 photo op，此处可扩展
    } catch (e) {
      api.trackEvent('inspection.sync.failed', { site_id: op.data.siteId || op.data.site_id, item_id: op.data.item_id, operation_id: op.id, error_code: (e && e.code) || 'sync_error' });
      if (e && e.status >= 400 && e.status < 500) {
        // 业务拒绝不会因重试而改变（例如超出 500m），移出队列并把原因交给页面展示。
        localStore.removeOp(op.id);
        summary.rejected.push({ id: op.id, type: op.type, error: e.error || '服务器拒绝了该操作' });
      }
      // 网络/服务端错误留待下次同步；不中断其余实体回放
    }
  }
  summary.remaining = localStore.getPending().length;
  return summary;
}

function flushLocalOps() {
  if (localFlushPromise) return localFlushPromise;
  localFlushPromise = flushLocalOpsInternal().finally(() => { localFlushPromise = null; });
  return localFlushPromise;
}

module.exports = { flushLocalOps };
