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

test('miniprogram enables required component lazy loading', () => {
  const appConfig = JSON.parse(fs.readFileSync(path.resolve(__dirname, '..', 'app.json'), 'utf8'));
  assert.equal(appConfig.lazyCodeLoading, 'requiredComponents');
});

function loadConfig(platform, envVersion, override) {
  const storage = {};
  if (override) storage.api_base_url_override = override;
  global.wx = {
    getSystemInfoSync: () => ({ platform }),
    getAccountInfoSync: () => ({ miniProgram: { envVersion } }),
    getStorageSync: (key) => storage[key] || '',
    setStorageSync: (key, value) => { storage[key] = value; },
    removeStorageSync: (key) => { delete storage[key]; },
  };
  delete require.cache[configPath];
  return { config: require(configPath), storage };
}

test('develop packages use the fixed development API on simulator and real devices', () => {
  for (const platform of ['devtools', 'ios', 'android', 'windows', 'mac']) {
    const { config } = loadConfig(platform, 'develop');
    assert.equal(config.BASE_URL, 'http://192.168.2.105:5000');
    assert.equal(config.API_PROFILE, 'local');
  }
});

test('trial and release packages always use the online API', () => {
  for (const envVersion of ['trial', 'release']) {
    for (const platform of ['devtools', 'ios', 'android', 'windows', 'mac']) {
      const { config } = loadConfig(platform, envVersion);
      assert.equal(config.BASE_URL, 'https://ops.hhyc-tec.cn');
      assert.equal(config.API_PROFILE, 'online');
    }
  }
});

test('legacy, expired, and malformed overrides do not affect the API profile', () => {
  for (const override of [
    'http://127.0.0.1:5020',
    { url: 'http://127.0.0.1:5020', expires_at: Date.now() - 1 },
    { url: 'https://ops.hhyc-tec.cn', expires_at: Date.now() + 60_000 },
  ]) {
    const develop = loadConfig('devtools', 'develop', override);
    assert.equal(develop.config.BASE_URL, 'http://192.168.2.105:5000');
    assert.equal(develop.config.API_PROFILE, 'local');
    assert.deepEqual(develop.storage.api_base_url_override, override);

    const trial = loadConfig('ios', 'trial', override).config;
    assert.equal(trial.BASE_URL, 'https://ops.hhyc-tec.cn');
    assert.equal(trial.API_PROFILE, 'online');
  }
});

test('unknown account environment fails closed to the online API', () => {
  const { config } = loadConfig('devtools', 'unknown');
  assert.equal(config.BASE_URL, 'https://ops.hhyc-tec.cn');
  assert.equal(config.API_PROFILE, 'online');
});
