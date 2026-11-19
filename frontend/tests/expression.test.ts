// 「自訂」的運算式：切詞與插入（docs/custom-expression-plan.md）。執行：npm test
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { insertAt, quote, tokenize, vocabulary, type SyntaxItem, type Token } from '../src/lib/expression.ts'

// 一小份 ui_syntax（真正的來自 Python 的語法表）
const SYNTAX: SyntaxItem[] = [
  { id: 'pl.col', insert: 'pl.col("")' },
  { id: 'pl.lit', insert: 'pl.lit()' },
  { id: 'lit.decimal', insert: '0.0', doc: '小數' },
  { id: '+', insert: ' + ' },
  { id: '-', insert: ' - ', doc: '兩個日期相減之後要接 .dt.total_days()' },
  { id: 'True', insert: 'True' },
  { id: 'fill_null', insert: '.fill_null()' },
  { id: 'round', insert: '.round(2)' },
  { id: 'pl.when', insert: 'pl.when().then().otherwise()', doc: '.then() 後面可以再接 .when().then()' },
  { id: 'str.strip_chars', insert: '.str.strip_chars()' },
  { id: 'str.contains', insert: '.str.contains("", literal=True)' },
  { id: 'dt.year', insert: '.dt.year()' },
  { id: 'cast', insert: '.cast(, strict=False)' },
  { id: 'pl.Int64', insert: '.cast(pl.Int64, strict=False)' },
]
const VOCAB = vocabulary(SYNTAX)

const join = (tokens: Token[]) => tokens.map((t) => t.text).join('')
const kinds = (text: string, columns: string[] | null = null) =>
  tokenize(text, VOCAB, columns)
    .filter((t) => t.kind !== 'space')
    .map((t) => [t.text, t.kind])

test('接回去和原本的文字一樣', () => {
  for (const text of [
    'pl.col("金額") - pl.col("折扣").fill_null(0)',
    '(pl.col("a")\n  + 1)  # 註解\n',
    'pl.col("沒有關',
    'pl.col（“a”）',
    '',
    '   \n\t ',
    'pl.lit("😀") + "👍🏽"',
    "'''三\n引號''' + '\\''",
  ]) {
    assert.equal(join(tokenize(text, VOCAB, null)), text)
  }
})

test('每一段的種類', () => {
  assert.deepEqual(kinds('pl.col("金額") - 0.9'), [
    ['pl', 'namespace'],
    ['.', 'punct'],
    ['col', 'function'],
    ['(', 'punct'],
    ['"金額"', 'column'],
    [')', 'punct'],
    ['-', 'operator'],
    ['0.9', 'number'],
  ])
  assert.deepEqual(kinds('True # 是'), [
    ['True', 'keyword'],
    ['# 是', 'comment'],
  ])
})

test('不在語法表的名稱是 unknown', () => {
  const kind = (text: string, name: string) => kinds(text).find(([t]) => t === name)?.[1]
  assert.equal(kind('pl.col("a").sum()', 'sum'), 'unknown')
  assert.equal(kind('pl.read_csv("x")', 'read_csv'), 'unknown')
  assert.equal(kind('os.system("x")', 'os'), 'unknown')
  assert.equal(kind('pl.col("a").str.year()', 'year'), 'unknown') // .str. 後面不是 .str. 的方法
  assert.equal(kind('pl.col("a").dt.strip_chars()', 'strip_chars'), 'unknown')
  assert.equal(kind('pl.col("a").str.strip_chars()', 'strip_chars'), 'function')
  assert.equal(kind('pl.col("a").dt.year()', 'year'), 'function')
  assert.equal(kind('pl.col("a").str.strip_chars()', 'str'), 'namespace')
  // .then 照一般的方法算（高亮不檢查前後文）
  assert.equal(kind('pl.col("a").then(1)', 'then'), 'function')
  assert.equal(kind('pl.when(x).then(1).when(y)', 'when'), 'function')
  assert.equal(kind('pl.Int64', 'Int64'), 'function')
})

test('欄：在清單裡是 column，不在是 error；清單是 null 時都是 column', () => {
  const columns = ['金額', '有"引號']
  assert.equal(kinds('pl.col("金額")', columns)[4][1], 'column')
  assert.equal(kinds('pl.col("金")', columns)[4][1], 'error')
  assert.equal(kinds('pl.col("金")', null)[4][1], 'column')
  assert.equal(kinds('pl.col("有\\"引號")', columns)[4][1], 'column')
  assert.equal(kinds("pl.col('金額')", columns)[4][1], 'column')
  // pl.col 以外的字串是一般的字串
  assert.equal(kinds('pl.lit("金")', columns)[4][1], 'string')
})

test('字串：單引號、雙引號、三引號、反斜線；沒有關的引號只到那一行的最後', () => {
  assert.deepEqual(kinds(`"a" + 'b' + """c\nd""" + "e\\"f"`).filter(([, k]) => k === 'string').length, 4)
  const tokens = tokenize('"沒有關\npl.col("a")', VOCAB, null)
  assert.deepEqual(tokens[0], { kind: 'error', text: '"沒有關' })
  assert.equal(tokens[1].text, '\n')
  assert.ok(tokens.some((t) => t.kind === 'column'))
})

test('關鍵字參數的名稱不算 unknown', () => {
  for (const name of ['strict', 'literal', 'separator', 'mode']) {
    const found = kinds(`f(${name}=1)`).find(([t]) => t === name)
    assert.notEqual(found?.[1], 'unknown', name)
  }
  assert.equal(kinds('x == 1')[0][1], 'unknown') // == 不是關鍵字參數
})

test('全形的符號是 error', () => {
  assert.deepEqual(
    kinds('pl.col（“a”）').filter(([, k]) => k === 'error').map(([t]) => t),
    ['（', '“', '”', '）'],
  )
})

test('vocabulary：四組名稱；運算子與字面值那幾項不會變成名稱', () => {
  assert.deepEqual([...VOCAB.pl].sort(), ['Int64', 'col', 'lit', 'when'])
  assert.deepEqual([...VOCAB.str].sort(), ['contains', 'strip_chars'])
  assert.deepEqual([...VOCAB.dt].sort(), ['total_days', 'year'])
  assert.deepEqual([...VOCAB.methods].sort(), ['cast', 'fill_null', 'otherwise', 'round', 'then', 'when'])
})

test('quote', () => {
  assert.equal(quote('金額'), '"金額"')
  assert.equal(quote('a b'), '"a b"')
  assert.equal(quote('有"引號'), '"有\\"引號"')
  assert.equal(quote('a\\b'), '"a\\\\b"')
  assert.equal(quote('\\"'), '"\\\\\\""')
})

test('insertAt', () => {
  assert.deepEqual(insertAt('ab', 1, 1, 'X'), { text: 'aXb', cursor: 2 })
  assert.deepEqual(insertAt('ab', 0, 0, 'X'), { text: 'Xab', cursor: 1 })
  assert.deepEqual(insertAt('ab', 2, 2, 'X'), { text: 'abX', cursor: 3 })
  assert.deepEqual(insertAt('abcd', 1, 3, 'X'), { text: 'aXd', cursor: 2 }) // 取代選取的文字
  assert.deepEqual(insertAt('a', 1, 1, '.fill_null()', 11), { text: 'a.fill_null()', cursor: 12 })
  assert.deepEqual(insertAt('', 0, 0, 'pl.col("")', 8), { text: 'pl.col("")', cursor: 8 })
  assert.deepEqual(insertAt('ab', 9, 9, 'X'), { text: 'abX', cursor: 3 }) // 超出範圍時接在最後
})
