const assert = require('assert');

const {
  hasInspectionFieldRecord,
  isPendingInspectionSubmit,
} = require('../utils/inspectionSubmissionState.js');

assert.equal(hasInspectionFieldRecord({}), false, '无备注、照片或完整校准信息时必须拦截');
assert.equal(hasInspectionFieldRecord({ calibrator: '张工' }), false, '不完整校准信息不能作为现场记录');
assert.equal(hasInspectionFieldRecord({ calibrationValues: '7.00' }), false, '不完整校准信息不能作为现场记录');
assert.equal(hasInspectionFieldRecord({ remark: '  已完成巡检  ' }), true);
assert.equal(hasInspectionFieldRecord({ photoCount: 1 }), true);
assert.equal(hasInspectionFieldRecord({ calibrator: '张工', calibrationValues: '7.00' }), true);

const pending = [{ type: 'submit', syncStatus: 'pending', data: { item_id: 10, plan_id: 20 } }];
assert.equal(isPendingInspectionSubmit(pending, 10, 20), true, '同一检查项的本地待同步记录必须阻止重复提交');
assert.equal(isPendingInspectionSubmit(pending, '10', '20'), true, '刷新后的字符串 ID 仍应正确识别');
assert.equal(isPendingInspectionSubmit(pending, 11, 20), false);
assert.equal(isPendingInspectionSubmit([{ ...pending[0], syncStatus: 'synced' }], 10, 20), false);

const storage = {};
global.wx = {
  getStorageSync: (key) => storage[key],
  setStorageSync: (key, value) => { storage[key] = value; },
};
const localStore = require('../utils/localStore.js');
const firstOperationId = localStore.addOp('submit', { item_id: 10, plan_id: 20, result: 'normal' });
const duplicateOperationId = localStore.addOp('submit', { item_id: 10, plan_id: 20, result: 'normal' });
assert.equal(duplicateOperationId, firstOperationId, '快速重复点击只能复用同一待同步实体');
assert.equal(localStore.queueCount(), 1, '同一检查项只能保留一条待同步记录');

console.log('inspectionSubmissionState tests passed');
