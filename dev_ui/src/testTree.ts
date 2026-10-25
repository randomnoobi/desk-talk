// Turns pytest's flat list of tests into folder → file → test, for the Checks tab.
import type { TestItem } from './api'

/** One folder, file or test. `testIds` is every test id under it (just its own for a test). */
export interface TreeNode {
  name: string                     // 'unit', 'test_a.py', 'test_x'
  path: string                     // 'tests/unit', 'tests/unit/test_a.py', or the test's id
  children: TreeNode[]             // a folder's files, a file's tests; none for a test
  testIds: string[]
  slow: boolean                    // a test marked slow (always false for folders and files)
}

/** The part of a test id after its file: 'tests/unit/test_a.py::TestX::test_y' → 'TestX::test_y'. */
export function testName(id: string): string {
  const cut = id.indexOf('::')
  return cut < 0 ? id : id.slice(cut + 2)
}

// 'tests/unit/sub/test_a.py' → folder 'unit' at 'tests/unit', file 'sub/test_a.py'.
// A file straight in the top folder ('tests/test_a.py') goes in a folder named after the top folder.
function splitFile(file: string): { folder: string, folderPath: string, fileName: string } {
  const parts = file.split('/')
  if (parts.length <= 2) {
    const top = parts.length === 2 ? parts[0] : '.'
    return { folder: top, folderPath: top, fileName: parts[parts.length - 1] }
  }
  return { folder: parts[1], folderPath: `${parts[0]}/${parts[1]}`, fileName: parts.slice(2).join('/') }
}

/** Folder, then file, then test, each in the order pytest found them. */
export function buildTree(items: TestItem[]): TreeNode[] {
  const folders = new Map<string, TreeNode>()
  const files = new Map<string, TreeNode>()
  for (const item of items) {
    const file = item.file || item.id.split('::')[0]
    const { folder, folderPath, fileName } = splitFile(file)
    let folderNode = folders.get(folderPath)
    if (!folderNode) {
      folderNode = { name: folder, path: folderPath, children: [], testIds: [], slow: false }
      folders.set(folderPath, folderNode)
    }
    let fileNode = files.get(file)
    if (!fileNode) {
      fileNode = { name: fileName, path: file, children: [], testIds: [], slow: false }
      files.set(file, fileNode)
      folderNode.children.push(fileNode)
    }
    fileNode.children.push({
      name: testName(item.id), path: item.id, children: [], testIds: [item.id], slow: item.markers.includes('slow'),
    })
    fileNode.testIds.push(item.id)
    folderNode.testIds.push(item.id)
  }
  return [...folders.values()]
}

/**
 * Only the tests whose id has `words` in it (any case), with the folders and files they're in.
 * Empty words give back the same tree.
 */
export function filterTree(tree: TreeNode[], words: string): TreeNode[] {
  const want = words.trim().toLowerCase()
  if (!want) return tree
  const keep = (node: TreeNode): TreeNode | null => {
    if (node.children.length === 0) return node.path.toLowerCase().includes(want) ? node : null
    const children = node.children.map(keep).filter((n): n is TreeNode => n !== null)
    if (children.length === 0) return null
    return { ...node, children, testIds: children.flatMap(c => c.testIds) }
  }
  return tree.map(keep).filter((n): n is TreeNode => n !== null)
}
