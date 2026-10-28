import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import ts from 'typescript'

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const projectRoot = resolve(frontendRoot, '..')
const menuPath = resolve(frontendRoot, 'src/router/menu.ts')
const routesPath = resolve(frontendRoot, 'src/router/routes.ts')
const manualPath = resolve(projectRoot, 'docs/用户手册.md')
const quickReferencePath = resolve(projectRoot, 'docs/AI助手模块速查.md')
const outputs = [
  resolve(projectRoot, 'backend/app/data/assistant_knowledge.json'),
  resolve(frontendRoot, 'src/generated/assistantRoutes.json'),
]

const sourceTexts = [menuPath, routesPath, manualPath, quickReferencePath].map((path) =>
  readFileSync(path, 'utf8'),
)
const [menuText, routesText, manualText, quickReferenceText] = sourceTexts

function prop(object, name) {
  if (!ts.isObjectLiteralExpression(object)) return undefined
  const member = object.properties.find(
    (item) =>
      ts.isPropertyAssignment(item) &&
      ((ts.isIdentifier(item.name) && item.name.text === name) ||
        (ts.isStringLiteral(item.name) && item.name.text === name)),
  )
  return member?.initializer
}

function literal(object, name) {
  const value = prop(object, name)
  return value && ts.isStringLiteral(value) ? value.text : undefined
}

function exportedMenuTree(sourceText) {
  const source = ts.createSourceFile('menu.ts', sourceText, ts.ScriptTarget.Latest, true)
  for (const statement of source.statements) {
    if (!ts.isVariableStatement(statement)) continue
    if (!statement.modifiers?.some((item) => item.kind === ts.SyntaxKind.ExportKeyword)) continue
    for (const declaration of statement.declarationList.declarations) {
      if (ts.isIdentifier(declaration.name) && declaration.name.text === 'menuTree') {
        return declaration.initializer
      }
    }
  }
  throw new Error('无法从 menu.ts 读取导出的 menuTree')
}

function collectMenuRoutes() {
  const initializer = exportedMenuTree(menuText)
  if (!initializer || !ts.isArrayLiteralExpression(initializer)) {
    throw new Error('menuTree 必须是对象数组')
  }
  const routes = []
  const visit = (items, parentPath = null, inheritedModule = null) => {
    for (const item of items) {
      if (!ts.isObjectLiteralExpression(item)) continue
      const name = literal(item, 'name')
      const title = literal(item, 'title')
      const path = literal(item, 'path')
      const permission = literal(item, 'permission')
      if (!name || !title || !path || !permission) {
        throw new Error('menuTree 条目必须定义 name/title/path/permission')
      }
      const module = permission.startsWith('module:')
        ? permission.slice('module:'.length)
        : inheritedModule
      const children = prop(item, 'children')
      const hasChildren = children && ts.isArrayLiteralExpression(children)
      routes.push({
        name,
        title,
        path,
        permission,
        module,
        parent_path: parentPath,
        is_leaf: !hasChildren,
      })
      if (hasChildren) visit(children.elements, path, module)
    }
  }
  visit(initializer.elements)
  return routes
}

function collectAdditionalRoutes() {
  const source = ts.createSourceFile('routes.ts', routesText, ts.ScriptTarget.Latest, true)
  const routes = []
  const visit = (node) => {
    if (ts.isObjectLiteralExpression(node)) {
      const name = literal(node, 'name')
      const rawPath = literal(node, 'path')
      const meta = prop(node, 'meta')
      const title = meta && literal(meta, 'title')
      const permission = meta && literal(meta, 'permission')
      if (name && rawPath && title && permission) {
        const path = rawPath.startsWith('/') ? rawPath : `/${rawPath}`
        const module = permission.startsWith('module:')
          ? permission.slice('module:'.length)
          : null
        routes.push({
          name,
          title,
          path,
          permission,
          module,
          parent_path: meta && literal(meta, 'parentPath') || null,
          is_leaf: true,
        })
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(source)
  return routes
}

function parseManualSections(text) {
  const sections = {}
  let currentTitle = null
  let currentLines = []
  const save = () => {
    if (!currentTitle) return
    sections[currentTitle] = currentLines
      .join('\n')
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
      .replace(/`([^`]+)`/g, '$1')
      .trim()
  }
  for (const line of text.split(/\r?\n/)) {
    const heading = line.match(/^##\s+(.+?)\s*$/)
    if (heading) {
      save()
      currentTitle = heading[1]
      currentLines = []
    } else if (currentTitle) {
      currentLines.push(line)
    }
  }
  save()
  return sections
}

function parseModuleGuides(text) {
  const guides = {}
  const titles = {}
  let currentModule = null
  let currentTitle = null
  let currentLines = []
  const save = () => {
    if (!currentModule) return
    guides[currentModule] = currentLines.join('\n').trim()
    titles[currentModule] = currentTitle
  }
  for (const line of text.split(/\r?\n/)) {
    const heading = line.match(/^##\s+(.+?)（([a-z_]+)）\s*$/u)
    if (heading) {
      save()
      currentTitle = heading[1]
      currentModule = heading[2]
      currentLines = []
    } else if (currentModule) {
      currentLines.push(line)
    }
  }
  save()
  return { guides, titles }
}

const routes = [...collectMenuRoutes(), ...collectAdditionalRoutes()]
const seenPaths = new Set()
for (const route of routes) {
  if (seenPaths.has(route.path)) throw new Error(`重复的助手路由路径：${route.path}`)
  seenPaths.add(route.path)
}
routes.sort((left, right) => left.path.localeCompare(right.path, 'zh-CN'))

const sections = parseManualSections(manualText)
const { guides: moduleGuides, titles: moduleGuideTitles } = parseModuleGuides(quickReferenceText)
const requiredModules = ['dashboard', 'purchase', 'product', 'order', 'stock', 'approval', 'finance', 'settings']
for (const module of requiredModules) {
  if (!moduleGuides[module]) throw new Error(`模块速查缺少权限条目：${module}`)
}
const knowledgeRoutes = routes.map((route) => {
  const hasGuide = Boolean(route.module && moduleGuides[route.module])
  return {
    ...route,
    guide_section: hasGuide ? route.module : null,
    guide_status: hasGuide ? 'available' : 'unavailable',
  }
})

const versionInput = JSON.stringify({
  menu: menuText,
  routes: routesText,
  manual: manualText,
  quickReference: quickReferenceText,
  requiredModules,
})
const version = `sha256:${createHash('sha256').update(versionInput).digest('hex')}`
const backendManifest = {
  version,
  sources: [
    'frontend/src/router/menu.ts',
    'frontend/src/router/routes.ts',
    'docs/用户手册.md',
    'docs/AI助手模块速查.md',
  ],
  routes: knowledgeRoutes,
  sections,
  module_guides: moduleGuides,
  module_guide_titles: moduleGuideTitles,
}
const frontendManifest = {
  version,
  routes: routes.map(({ name, title, path, permission, module, parent_path, is_leaf }) => ({
    name,
    title,
    path,
    permission,
    module,
    parent_path,
    is_leaf,
  })),
}
const contents = [
  `${JSON.stringify(backendManifest, null, 2)}\n`,
  `${JSON.stringify(frontendManifest, null, 2)}\n`,
]

if (process.argv.includes('--check')) {
  const stale = outputs.filter((path, index) => {
    try {
      return readFileSync(path, 'utf8') !== contents[index]
    } catch {
      return true
    }
  })
  if (stale.length) {
    console.error(`助手知识清单过期，请运行 npm run assistant:knowledge：\n${stale.join('\n')}`)
    process.exitCode = 1
  } else {
    console.log(`助手知识清单一致：${version}；覆盖 ${routes.length} 条路由`)
  }
} else {
  outputs.forEach((path, index) => {
    mkdirSync(dirname(path), { recursive: true })
    writeFileSync(path, contents[index], 'utf8')
  })
  console.log(`已生成助手知识清单：${version}；覆盖 ${routes.length} 条路由`)
}
