const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const componentPath = path.join(__dirname, '../components/custom-navbar/custom-navbar.js');
const navbarViewPath = path.join(__dirname, '../components/custom-navbar/custom-navbar.wxml');
const indexViewPath = path.join(__dirname, '../pages/index/index.wxml');
const planViewPath = path.join(__dirname, '../pages/plan/plan.wxml');

function registerCustomNavbar() {
  let definition;
  const originalComponent = global.Component;
  global.Component = value => { definition = value; };
  delete require.cache[require.resolve(componentPath)];
  try {
    require(componentPath);
  } finally {
    if (originalComponent === undefined) delete global.Component;
    else global.Component = originalComponent;
    delete require.cache[require.resolve(componentPath)];
  }
  return definition;
}

test('custom navbar enables named slots while page actions stay in their approved content areas', () => {
  const definition = registerCustomNavbar();
  assert.equal(definition.options.multipleSlots, true);
  assert.equal(definition.options.styleIsolation, 'apply-shared');

  const navbarView = fs.readFileSync(navbarViewPath, 'utf8');
  assert.match(navbarView, /<slot name="left"><\/slot>[\s\S]*<slot name="center"><\/slot>[\s\S]*<slot name="right"><\/slot>/);

  const indexView = fs.readFileSync(indexViewPath, 'utf8');
  assert.match(indexView, /greeting-area[\s\S]*?bindtap="goMessages"/);

  const planView = fs.readFileSync(planViewPath, 'utf8');
  assert.match(planView, /tools-row[\s\S]*?bindtap="onOpenFavorites"[\s\S]*?bindtap="onNewPlan"/);
});
