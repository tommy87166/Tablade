// 「檢視」分頁的純函式（docs/cell-view-plan.md）。執行：npm test（node --test，不用裝任何套件）
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { cellView, sourceText, type CellValue } from '../src/lib/cell.ts'

const COLUMNS = ['品名', '數量', '__source_file', '__line_no']
const zh = (name: string) => `［${name}］`
const en = (name: string) => ` [${name}]`

test('一般的文字格', () => {
  const view = cellView(COLUMNS, ['蘋果汁', 3, 'a.txt', 12], 41, '品名', 'String')
  assert.deepEqual(view, {
    column: '品名',
    dtype: 'String',
    row: 42,
    value: '蘋果汁',
    source: { file: 'a.txt', sheet: null, line: 12 },
    chars: 3,
  })
})

test('來源：有工作表、都沒有、只有其中一個', () => {
  const sheet = cellView(['a', '__source_file', '__sheet', '__line_no'], ['x', 'b.xlsx', '一月', 5], 0, 'a', null)
  assert.deepEqual(sheet?.source, { file: 'b.xlsx', sheet: '一月', line: 5 })
  assert.equal(cellView(['a'], ['x'], 0, 'a', null)?.source, null)
  assert.deepEqual(cellView(['a', '__line_no'], ['x', 7], 0, 'a', null)?.source, { file: null, sheet: null, line: 7 })
  assert.deepEqual(cellView(['a', '__source_file'], ['x', 'c.txt'], 0, 'a', null)?.source, { file: 'c.txt', sheet: null, line: null })
})

test('各種值', () => {
  const values: [CellValue, number | null][] = [
    [null, null],
    ['', 0],
    ['   ', 3],
    ['a\tb\nc', 5],
    [12.5, null],
    [true, null],
    [false, null],
    ['x'.repeat(100_000), 100_000],
  ]
  for (const [value, chars] of values) {
    const view = cellView(['v'], [value], 0, 'v', null)
    assert.equal(view?.value, value)
    assert.equal(view?.chars, chars)
  }
})

test('字元數：中文、表情符號算一個字', () => {
  assert.equal(cellView(['v'], ['門市'], 0, 'v', null)?.chars, 2)
  assert.equal(cellView(['v'], ['😀a'], 0, 'v', null)?.chars, 2)
  assert.equal(cellView(['v'], ['👍🏽'], 0, 'v', null)?.chars, 2) // 膚色修飾是另一個碼位
})

test('小數、日期用文字送過來：不算字數', () => {
  assert.equal(cellView(['v'], ['18960.00'], 0, 'v', 'Decimal(38,2)')?.chars, null)
  assert.equal(cellView(['v'], ['2026-01-05'], 0, 'v', 'Date')?.chars, null)
  assert.equal(cellView(['v'], ['北區'], 0, 'v', 'String')?.chars, 2)
})

test('欄不在欄名裡：null', () => {
  assert.equal(cellView(COLUMNS, ['a', 1, 'f', 2], 0, '單價', null), null)
})

test('sourceText：和警告分頁原本的位置寫法相同', () => {
  assert.equal(sourceText('a.txt', null, 12, zh), 'a.txt:12')
  assert.equal(sourceText('b.xlsx', '一月', 3, zh), 'b.xlsx［一月］:3')
  assert.equal(sourceText('b.xlsx', '一月', 3, en), 'b.xlsx [一月]:3')
  assert.equal(sourceText('a.txt', undefined, 12, zh, '明細'), '@明細／a.txt:12')
  assert.equal(sourceText('a.txt', '', 12, zh, null), 'a.txt:12')
  // 原始的來源（原本的寫法：值是 null 時照樣印出 null）
  assert.equal(sourceText(null, null, null, zh), 'null:null')
})
