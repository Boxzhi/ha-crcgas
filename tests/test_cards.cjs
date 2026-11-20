// Run: node --test tests/test_cards.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const paths = ['custom_components/crcgas/frontend/crcgas-statistics-card.js',
  'www/community/crcgas-card/crcgas-statistics-card.js', 'www/community/crcgas-card/crcgas-card.js'];
for (const file of paths) {
  test(file + ' loads without a browser', () => {
    const code = fs.readFileSync(path.join(root,file),'utf8');
    const ctx = vm.createContext({HTMLElement: class {}, customElements:{define(){}}, window:{}});
    vm.runInContext(code, ctx);
    if (file.includes('statistics')) {
      assert.equal(vm.runInContext('_numeric("unknown")',ctx), null);
      assert.equal(vm.runInContext('_numeric("0")',ctx), 0);
      assert.equal(vm.runInContext('_numeric("")',ctx), null);
      assert.equal(vm.runInContext('_sumKnown([{cost:0}], "cost")',ctx), 0);
      assert.equal(vm.runInContext('_sumKnown([{cost:null}], "cost")',ctx), null);
      assert.equal(vm.runInContext('_sumKnown([], "cost")',ctx), null);
      const output = vm.runInContext('_tierProgress({step1Limit:420,step1Sum:43,step1Remain:377,step2Limit:240,step2Sum:0,step2Remain:240})',ctx);
      assert.match(output,/43\/420/);
      assert.match(output,/0\/240/);
      assert.match(output,/10\.2%/);
      assert.match(vm.runInContext('_tierProgress({step1Limit:420,step1Sum:43,step1Remain:0})',ctx), /暂无/);
    }
    assert.doesNotMatch(code,/step1Limit\s*\|\|\s*330|step1Price\s*\|\|\s*3\.1|const t1 = 5/);
  });
}
test('statistics copies identical', () => {
  assert.equal(fs.readFileSync(path.join(root,paths[0]),'utf8'),fs.readFileSync(path.join(root,paths[1]),'utf8'));
});
