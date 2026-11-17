// Offline artifact hygiene only; not a production network or WeChat review gate.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const sensitiveName = /^(?:\.env(?:\..*)?|app-secret\.txt|product-session\.json|gateway-token\.txt|device\.env|conv_ai_config\.json|.*\.(?:sqlite3?|db)(?:-(?:wal|shm|journal))?|.*\.(?:pem|p12|pfx|key)|id_(?:rsa|ed25519)(?:\.pub)?)$/i
const literalCredential = /(?:app[_-]?secret|product[_-]?secret|device[_-]?secret|api[_-]?key|session[_-]?token)["']?\s*[:=]\s*["'][A-Za-z0-9_.:/+=-]{16,}["']/i
const privateKey = /-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----/
const textFile = /\.(?:ts|js|json|wxml|wxss|txt|md|ya?ml|xml|ini|conf)$/i

export function checkPackageSafety(root) {
  const config = JSON.parse(fs.readFileSync(path.join(root, 'project.config.json'), 'utf8'))
  const rules = config.packOptions?.ignore || []
  if (!Array.isArray(rules) || !Array.isArray(config.packOptions?.include || []) || config.packOptions?.include?.length) {
    throw Error('Package include overrides require an explicit safety review')
  }
  const matchers = rules.map(rule => {
    if (typeof rule.value !== 'string') throw Error('Invalid package exclusion')
    const value = rule.value.replace(/^\/+|\/+$/g, '')
    if (!value) throw Error('Empty package exclusion')
    switch (rule.type) {
      case 'folder': return name => name === value || name.startsWith(value + '/')
      case 'file': return name => name === value
      case 'suffix': return name => name.endsWith(value)
      case 'prefix': return name => name.startsWith(value)
      case 'regexp': { const pattern = new RegExp(value); return name => pattern.test(name) }
      default: throw Error('Unsupported package exclusion')
    }
  })
  let files = 0, bytes = 0
  function walk(relative = '') {
    for (const entry of fs.readdirSync(path.join(root, relative), { withFileTypes: true })) {
      const name = relative ? relative + '/' + entry.name : entry.name
      if (matchers.some(matches => matches(name))) continue
      if (entry.isSymbolicLink()) throw Error('Package contains a symbolic link: ' + name)
      if (entry.isDirectory()) {
        if (['.git', '.data', '.venv'].includes(entry.name)) throw Error('Package contains a private directory: ' + name)
        walk(name)
      } else if (entry.isFile()) {
        if (sensitiveName.test(entry.name)) throw Error('Package contains a sensitive file: ' + name)
        const data = fs.readFileSync(path.join(root, name))
        if (textFile.test(entry.name) && (literalCredential.test(data.toString('utf8')) || privateKey.test(data.toString('utf8')))) {
          // Deliberately report only the filename, never matched text or values.
          throw Error('Package contains a possible credential: ' + name)
        }
        files++; bytes += data.length
      } else throw Error('Package contains a non-regular entry: ' + name)
    }
  }
  walk()
  return { files, bytes }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2)
  if (args.length && (args.length !== 2 || args[0] !== '--project-dir')) {
    throw Error('Usage: check-package-safety.mjs [--project-dir DIRECTORY]')
  }
  const result = checkPackageSafety(args.length ? path.resolve(args[1]) : path.resolve(import.meta.dirname, '..'))
  console.log(`package-safety: ${result.files} included files, ${result.bytes} bytes; no known private artifacts`)
}
