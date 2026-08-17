import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {
  inspectionConfigFormValues, inspectionItemAccessState, inspectionItemFormValues,
  inspectionItemRefreshNotice,
} from './inspectionSettingsForm.js';

test('normalizes persisted photo flag and sort order for the item form', () => {
  assert.deepEqual(inspectionItemFormValues({
    item_name: '浊度',
    category: '水质',
    frequency_level: 'high',
    photo_required: 1,
    need_review: 1,
    max_photos: '2',
    inspection_standard: '读数在合格范围内',
    sort_order: '3',
  }, 5), {
    item_name: '浊度',
    category: '水质',
    frequency_level: 'high',
    photo_required: true,
    need_review: true,
    max_photos: 2,
    inspection_standard: '读数在合格范围内',
    sort_order: 3,
  });
});

test('provides stable defaults for a newly added item', () => {
  assert.deepEqual(inspectionItemFormValues(null, 2), {
    item_name: '',
    category: '',
    frequency_level: 'mid',
    photo_required: false,
    need_review: false,
    max_photos: 0,
    inspection_standard: '',
    sort_order: 3,
  });
});

test('normalizes config enabled state for a Select control', () => {
  assert.deepEqual(inspectionConfigFormValues({
    site_type: 'water_quality',
    template_id: 4,
    device_types: '["ph"]',
    is_active: 0,
  }), {
    site_type: 'water_quality',
    template_id: 4,
    device_types: ['ph'],
    remark: '',
    is_active: false,
  });
  assert.equal(inspectionConfigFormValues(null).is_active, true);
});

test('item load failure is not presented as an editable empty list', () => {
  assert.deepEqual(inspectionItemAccessState({ loading: false, error: '读取失败', saving: false }), {
    canMutate: false, showEmpty: false,
  });
  assert.deepEqual(inspectionItemAccessState({ loading: false, error: '', saving: false }), {
    canMutate: true, showEmpty: true,
  });
});

test('a successful write remains successful when the following refresh fails', () => {
  assert.equal(inspectionItemRefreshNotice(true), '');
  assert.match(inspectionItemRefreshNotice(false), /已保存.*刷新失败/);
});

test('item and config dialogs prevent duplicate actions while saving', () => {
  const source = fs.readFileSync(path.join(
    import.meta.dirname, 'InspectionSettingsPage.jsx',
  ), 'utf8');
  assert.match(source, /open=\{itemOpen\}[\s\S]*?confirmLoading=\{itemSaving\}/);
  assert.match(source, /onCancel=\{\(\) => !itemSaving && setItemOpen\(false\)\}/);
  assert.match(source, /open=\{configOpen\}[\s\S]*?confirmLoading=\{configSaving\}/);
  assert.match(source, /onCancel=\{\(\) => !configSaving && setConfigOpen\(false\)\}/);
});
