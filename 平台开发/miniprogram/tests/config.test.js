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
  const now = Date.now();
  assert.equal(config.setApiBaseOverride('http://127.0.0.1:5020', now), true);
  assert.deepEqual(storage.api_base_url_override, {
    url: 'http://127.0.0.1:5020',
    expires_at: now + config.API_OVERRIDE_TTL_MS,
  });
  config.clearApiOverride();
  assert.equal(storage.api_base_url_override, undefined);

  const overridden = loadConfig('devtools', {
    url: 'http://127.0.0.1:5020', expires_at: Date.now() + 60_000,
  }).config;
  assert.equal(overridden.BASE_URL, 'http://127.0.0.1:5020');
  assert.equal(overridden.API_PROFILE, 'local');
});

test('legacy and expired local overrides are cleared without production fallback credentials', () => {
  const legacy = loadConfig('devtools', 'http://127.0.0.1:5020');
  assert.equal(legacy.config.BASE_URL, 'https://ops.hhyc-tec.cn');
  assert.equal(legacy.storage.api_base_url_override, undefined);

  const expired = loadConfig('devtools', {
    url: 'http://127.0.0.1:5020', expires_at: Date.now() - 1,
  });
  assert.equal(expired.config.BASE_URL, 'https://ops.hhyc-tec.cn');
  assert.equal(expired.storage.api_base_url_override, undefined);
});

test('real devices always use the online API', () => {
  const { config } = loadConfig('ios', {
    url: 'http://127.0.0.1:5020', expires_at: Date.now() + 60_000,
  });
  assert.equal(config.BASE_URL, 'https://ops.hhyc-tec.cn');
  assert.equal(config.API_PROFILE, 'online');
  assert.equal(config.setApiBaseOverride('http://127.0.0.1:5020'), false);
});
