import fs from 'node:fs/promises'
import path from 'node:path'
import sharp from 'sharp'

// Keep vector sources editable, but use local PNGs at runtime for predictable
// rendering in the native simulator and on phones. No network is used.
const root = path.resolve(import.meta.dirname, '..')
const directory = path.join(root, 'assets', 'icons')
const icons = (await fs.readdir(directory)).filter((name) => name.endsWith('.svg'))
for (const name of icons) {
  await sharp(path.join(directory, name), { density: 192 })
    .resize(72, 72)
    .png({ compressionLevel: 9 })
    .toFile(path.join(directory, name.replace(/\.svg$/, '.png')))
}
console.log(`local-assets: ${icons.length} PNG icons ready`)
