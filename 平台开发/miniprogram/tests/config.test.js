const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');

const configPath = require.resolve('../utils/config.js');

test('repository root is the only WeChat project entry', () => {
  const root = path.resolve(__dirname, '..', '..');
  const rootProject = JSON.parse(fs.readFileSync(path.join(root, 'project.config.json'), 'utf8'));
  assert.equal(rootProject.miniprogramRoot, 'miniprogram/');
  assert.equal(rootProject.libVersion, '3.17.0');
  assert.equal(fs.existsSync(path.join(root, 'miniprogram', 'project.config.json')), false);
  assert.equal(fs.existsSync(path.join(root, 'miniprogram', 'project.config.legacy.json')), false);
  assert.equal(fs.existsSync(path.join(root, 'project.private.config.json')), true);
  assert.equal(fs.existsSync(path.join(root, 'miniprogram', 'project.private.config.json')), true);
});

function loadConfig(platform, override) {
  const storage = {};
  if (override) storage.api_base_url_override = override;
  global.wx = {
    getSystemInfoSync: () => ({ platform }),
    getStorageSync: (key) => storage[key] || '',
    setStorageSync: (key, value) => { storage[key] = value; },
    removeStorageSync: (key) => { delete storage[key]; },
  };
  delete require.cache[configPath];
  return { config: require(configPath), storage };
}

test('devtools defaults to the online API', () => {
  const { config } = loadConfig('devtools');
  assert.equal(config.BASE_URL, 'https://ops.hhyc-tec.cn');
  assert.equal(config.API_PROFILE, 'online');
});

test('devtools can explicitly select and clear the local API', () => {
  const { config, storage } = loadConfig('devtools');
  assert.equal(config.setApiBaseOverride('http://127.0.0.1:5020'), true);
  assert.equal(storage.api_base_url_override, 'http://127.0.0.1:5020');
  config.clearApiOverride();
  assert.equal(storage.api_base_url_override, undefined);

  const overridden = loadConfig('devtools', 'http://127.0.0.1:5020').config;
  assert.equal(overridden.BASE_URL, 'http://127.0.0.1:5020');
  assert.equal(overridden.API_PROFILE, 'local');
});

test('real devices always use the online API', () => {
  const { config } = loadConfig('ios', 'local');
  assert.equal(config.BASE_URL, 'https://ops.hhyc-tec.cn');
  assert.equal(config.API_PROFILE, 'online');
  assert.equal(config.setApiBaseOverride('http://127.0.0.1:5020'), false);
});
