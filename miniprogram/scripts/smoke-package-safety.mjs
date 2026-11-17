import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { checkPackageSafety } from './check-package-safety.mjs'

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-cat-package-safety-'))
let passed = 0
function fixture(test, config = { packOptions: { ignore: [{ type: 'folder', value: 'scripts' }], include: [] } }) {
  const root = fs.mkdtempSync(path.join(temporary, 'case-'))
  fs.writeFileSync(path.join(root, 'project.config.json'), JSON.stringify(config))
  test(root); passed++
}
try {
  fixture(root => {
    fs.writeFileSync(path.join(root, 'app.ts'), 'const apiKey = response.apiKey\n')
    assert.equal(checkPackageSafety(root).files, 2)
  })
  for (const name of ['.env', '.env.local', 'app-secret.txt', 'product-session.json', 'gateway-token.txt', 'device.env', 'conv_ai_config.json', 'state.sqlite3', 'state.db-wal', 'private.pem']) {
    fixture(root => {
      fs.writeFileSync(path.join(root, name), 'test fixture, not a real secret')
      assert.throws(() => checkPackageSafety(root), /sensitive file/)
    })
  }
  for (const source of ['const appSecret = "fixture_secret_0123456789"', '{"product_secret":"fixture_secret_0123456789"}', '-----BEGIN OPENSSH PRIVATE KEY-----']) {
    fixture(root => {
      fs.writeFileSync(path.join(root, 'app.ts'), source)
      assert.throws(() => checkPackageSafety(root), error => {
        assert.ok(!error.message.includes('fixture_secret_'))
        return /possible credential: app.ts/.test(error.message)
      })
    })
  }
  fixture(root => {
    fs.mkdirSync(path.join(root, 'scripts'))
    fs.writeFileSync(path.join(root, 'scripts', '.env'), 'excluded fixture')
    assert.equal(checkPackageSafety(root).files, 1)
  })
  fixture(root => {
    fs.mkdirSync(path.join(root, '.data'))
    assert.throws(() => checkPackageSafety(root), /private directory/)
  })
  fixture(root => {
    fs.symlinkSync(path.join(root, 'project.config.json'), path.join(root, 'copied-config.json'))
    assert.throws(() => checkPackageSafety(root), /symbolic link/)
  })
  fixture(root => assert.throws(() => checkPackageSafety(root), /include overrides/), { packOptions: { ignore: [], include: [{ type: 'file', value: '.env' }] } })
  fixture(root => assert.throws(() => checkPackageSafety(root), /Unsupported/), { packOptions: { ignore: [{ type: 'unknown', value: 'x' }] } })
  console.log(`package-safety-smoke: ${passed}/${passed} passed`)
} finally {
  // Only our mkdtemp-owned fixture tree; never a caller-provided directory.
  fs.rmSync(temporary, { recursive: true, force: true })
}
