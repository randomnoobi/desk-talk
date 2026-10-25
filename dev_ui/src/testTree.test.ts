import { describe, expect, it } from 'vitest'
import type { TestItem } from './api'
import { buildTree, filterTree, testName } from './testTree'

const item = (id: string, markers: string[] = []): TestItem => ({ id, file: id.split('::')[0], markers })

describe('buildTree', () => {
  it('puts tests under their folder, then their file', () => {
    const tree = buildTree([
      item('tests/unit/test_a.py::test_x'),
      item('tests/unit/test_a.py::test_y'),
      item('tests/integration/test_b.py::test_z'),
    ])

    expect(tree.map(n => n.name)).toEqual(['unit', 'integration'])
    const unit = tree[0]
    expect(unit.path).toBe('tests/unit')
    expect(unit.children).toHaveLength(1)
    expect(unit.testIds).toEqual(['tests/unit/test_a.py::test_x', 'tests/unit/test_a.py::test_y'])

    const file = unit.children[0]
    expect(file.name).toBe('test_a.py')
    expect(file.path).toBe('tests/unit/test_a.py')
    expect(file.testIds).toHaveLength(2)
    expect(file.children.map(t => [t.name, t.path, t.testIds])).toEqual([
      ['test_x', 'tests/unit/test_a.py::test_x', ['tests/unit/test_a.py::test_x']],
      ['test_y', 'tests/unit/test_a.py::test_y', ['tests/unit/test_a.py::test_y']],
    ])
    expect(file.children[0].children).toEqual([])
  })

  it('keeps the order pytest found them in, and a file in a folder inside one keeps its inner folder in its name', () => {
    const tree = buildTree([
      item('tests/slow/test_s.py::test_big', ['slow']),
      item('tests/characterization/test_c.py::test_c'),
      item('tests/unit/sub/test_d.py::TestThing::test_d[a/b]'),
      item('tests/slow/test_s.py::test_other', ['slow']),
    ])
    expect(tree.map(n => n.name)).toEqual(['slow', 'characterization', 'unit'])
    expect(tree[0].testIds).toEqual(['tests/slow/test_s.py::test_big', 'tests/slow/test_s.py::test_other'])
    expect(tree[2].children[0].name).toBe('sub/test_d.py')
    expect(tree[2].children[0].children[0].name).toBe('TestThing::test_d[a/b]')
  })

  it('puts a test straight in tests/ under a folder named tests', () => {
    const tree = buildTree([item('tests/test_top.py::test_t')])
    expect(tree.map(n => [n.name, n.path])).toEqual([['tests', 'tests']])
    expect(tree[0].children[0].name).toBe('test_top.py')
  })

  it('gives nothing for no tests', () => {
    expect(buildTree([])).toEqual([])
  })
})

describe('testName', () => {
  it('is the part after the file', () => {
    expect(testName('tests/unit/test_a.py::TestX::test_y[1]')).toBe('TestX::test_y[1]')
    expect(testName('tests/unit/test_a.py')).toBe('tests/unit/test_a.py')
  })
})

describe('filterTree', () => {
  const tree = buildTree([
    item('tests/unit/test_a.py::test_x'),
    item('tests/unit/test_a.py::test_y'),
    item('tests/integration/test_b.py::test_z'),
  ])

  it('keeps only the tests whose id has the words, and their folders and files', () => {
    const kept = filterTree(tree, 'TEST_Y')
    expect(kept.map(n => n.name)).toEqual(['unit'])
    expect(kept[0].testIds).toEqual(['tests/unit/test_a.py::test_y'])
    expect(kept[0].children[0].children.map(t => t.name)).toEqual(['test_y'])
  })

  it('keeps everything for empty words', () => {
    expect(filterTree(tree, '  ')).toBe(tree)
  })
})
