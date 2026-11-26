/**
 * 构建后处理：剥掉字体 CSS 里的 `.woff` 兜底。
 *
 * fontsource 的 @font-face 同时给 woff2 与 woff 两份源文件。
 * 内网客户端全是现代浏览器，woff2 全都认 —— 那 6MB 的 .woff
 * 只是让静态包白白大一倍（dist 11MB → 5MB）。
 *
 * 只动构建产物，不碰 node_modules 里的原始文件。
 */
import { readdir, readFile, writeFile, unlink } from 'node:fs/promises'
import { join } from 'node:path'

const ASSETS = new URL('../dist/assets/', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')

const files = await readdir(ASSETS)
const cssFiles = files.filter((f) => f.endsWith('.css'))
if (cssFiles.length === 0) {
  console.log('[strip-fonts] 没有 CSS 需要处理')
  process.exit(0)
}

const dropped = new Set()

for (const name of cssFiles) {
  const path = join(ASSETS, name)
  const original = await readFile(path, 'utf8')

  // 记录被剥掉的 .woff 资源名（只处理外链的，data URI 不落盘）
  for (const m of original.matchAll(/url\(\/assets\/([^)"']+\.woff)\)/g)) {
    dropped.add(m[1])
  }

  const stripped = original.replace(
    /,url\((?:data:font\/woff;base64,[^)]*|[^)]*\.woff)\)format\(["']woff["']\)/g,
    '',
  )

  if (stripped !== original) {
    await writeFile(path, stripped, 'utf8')
    console.log(`[strip-fonts] 已清理 ${name}`)
  }
}

let removed = 0
for (const name of dropped) {
  try {
    await unlink(join(ASSETS, name))
    removed += 1
  } catch {
    /* 已经不在了就无所谓 */
  }
}
console.log(`[strip-fonts] 删除 ${removed} 个 .woff 兜底文件`)
