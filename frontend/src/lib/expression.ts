/**
 * 「自訂」的運算式：語法高亮的切詞與插入（docs/custom-expression-plan.md）。
 * 純函式、沒有任何 import：frontend/tests/expression.test.ts 用 node --test 直接跑。
 *
 * 函式的名稱不寫死：從 schema 的 ui_syntax（Python 的語法表）整理出來（vocabulary）。
 * 高亮只是幫忙看，對錯以 Python 的驗證為準：標成 unknown 的 Python 一定會擋，反過來不一定。
 */

export type TokenKind =
  | 'function'
  | 'unknown'
  | 'namespace'
  | 'column'
  | 'error'
  | 'string'
  | 'number'
  | 'keyword'
  | 'comment'
  | 'operator'
  | 'punct'
  | 'space'

export interface Token {
  kind: TokenKind
  text: string
}

/** 語法表的一項（ui_syntax；這裡只用得到這幾個欄位） */
export interface SyntaxItem {
  id: string
  insert: string
  doc?: string
}

/** 認得的名稱：pl. 後面的、.str. 後面的、.dt. 後面的、其他的方法 */
export interface Vocabulary {
  pl: Set<string>
  str: Set<string>
  dt: Set<string>
  methods: Set<string>
}

const ROOT = 'pl'
const COLUMN = 'col'
const SPACES = ['str', 'dt'] as const
const KEYWORDS = new Set(['True', 'False', 'None'])
const OPERATORS = ['**', '//', '==', '!=', '>=', '<=', '<<', '>>', ':=', '->', '+', '-', '*', '/', '%', '<', '>', '&', '|', '~', '^', '@', '=']
const PUNCT = '()[]{},.:;'
const IDENT = /[\p{L}_][\p{L}\p{N}_]*/uy
const NUMBER = /(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?\d+)?[jJ]?/y

/** 從 ui_syntax 的插入文字與說明整理出認得的名稱（運算子、字面值那幾項不會變成名稱）。 */
export function vocabulary(items: SyntaxItem[]): Vocabulary {
  const vocab: Vocabulary = { pl: new Set(), str: new Set(), dt: new Set(), methods: new Set() }
  for (const item of items) {
    let text = `${item.insert} ${item.doc ?? ''}`
    text = text.replace(new RegExp(`\\b${ROOT}\\.(\\w+)`, 'g'), (_, name: string) => {
      vocab.pl.add(name)
      return ' '
    })
    for (const space of SPACES) {
      text = text.replace(new RegExp(`\\.${space}\\.(\\w+)`, 'g'), (_, name: string) => {
        vocab[space].add(name)
        return ' '
      })
    }
    for (const match of text.matchAll(/\.(\w+)\s*\(/g)) vocab.methods.add(match[1])
  }
  return vocab
}

/** 欄名放進 pl.col("…") 的字串：前後加引號，裡面的 \ 與 " 加反斜線。 */
export function quote(name: string): string {
  const escaped = name.split('\\').join('\\\\').split('"').join('\\"')
  return '"' + escaped + '"'
}

/** 字串的內容（拿掉引號、處理反斜線），用來比對欄名。 */
function unquote(text: string, quoteLength: number): string {
  const body = text.slice(quoteLength, text.length - quoteLength)
  const escapes: Record<string, string> = { n: '\n', t: '\t', r: '\r', '0': '\0' }
  return body.replace(/\\(.)/gsu, (_, ch: string) => escapes[ch] ?? ch)
}

/** 從 start 起的字串（含引號）有多長；沒有關的引號回傳 -1 與到哪裡為止。 */
function scanString(text: string, start: number): { end: number; closed: boolean; quoteLength: number } {
  const q = text[start]
  const triple = text.startsWith(q.repeat(3), start)
  const close = triple ? q.repeat(3) : q
  let i = start + close.length
  while (i < text.length) {
    const ch = text[i]
    if (ch === '\\') {
      i += 2
      continue
    }
    if (!triple && ch === '\n') break // 一般的字串不能跨行：沒有關的引號只到那一行的最後
    if (text.startsWith(close, i)) return { end: i + close.length, closed: true, quoteLength: close.length }
    i += 1
  }
  return { end: Math.min(i, text.length), closed: false, quoteLength: close.length }
}

/**
 * 切詞：每一段接回去和原本的文字完全一樣。columns 是這一步輸入的欄（還不知道時是 null：欄都當成對的）。
 */
export function tokenize(text: string, vocab: Vocabulary, columns: string[] | null): Token[] {
  const tokens: Token[] = []
  /** 有意義的段（不含空白與註解）在 tokens 裡的位置 */
  const meaningful: number[] = []
  const push = (kind: TokenKind, value: string) => {
    tokens.push({ kind, text: value })
    if (kind !== 'space' && kind !== 'comment') meaningful.push(tokens.length - 1)
  }
  const back = (n: number): Token | undefined => tokens[meaningful[meaningful.length - n]]
  const known = columns ? new Set(columns) : null
  let i = 0
  while (i < text.length) {
    const ch = text[i]
    if (/\s/u.test(ch)) {
      let j = i + 1
      while (j < text.length && /\s/u.test(text[j])) j++
      push('space', text.slice(i, j))
      i = j
      continue
    }
    if (ch === '#') {
      let j = text.indexOf('\n', i)
      if (j < 0) j = text.length
      push('comment', text.slice(i, j))
      i = j
      continue
    }
    if (ch === '"' || ch === "'") {
      const { end, closed, quoteLength } = scanString(text, i)
      const value = text.slice(i, end)
      // pl.col( 後面的第一個字串是欄名
      const isColumn =
        back(1)?.text === '(' && back(2)?.text === COLUMN && back(3)?.text === '.' && back(4)?.text === ROOT && back(4)?.kind === 'namespace'
      if (!closed) push('error', value)
      else if (isColumn) push(known && !known.has(unquote(value, quoteLength)) ? 'error' : 'column', value)
      else push('string', value)
      i = end
      continue
    }
    NUMBER.lastIndex = i
    const number = NUMBER.exec(text)
    if (number && /[\d.]/.test(ch) && (ch !== '.' || /\d/.test(text[i + 1] ?? ''))) {
      push('number', number[0])
      i += number[0].length
      continue
    }
    IDENT.lastIndex = i
    const ident = IDENT.exec(text)
    if (ident) {
      const name = ident[0]
      const after = text.slice(i + name.length)
      const prev = back(1)
      let kind: TokenKind
      if (KEYWORDS.has(name)) kind = 'keyword'
      else if (prev?.text === '.') {
        const owner = back(2)
        const space = SPACES.find((s) => owner?.text === s && owner.kind === 'namespace')
        if (owner?.text === ROOT && owner.kind === 'namespace') kind = vocab.pl.has(name) ? 'function' : 'unknown'
        else if (space) kind = vocab[space].has(name) ? 'function' : 'unknown'
        else if ((SPACES as readonly string[]).includes(name) && /^\s*\./u.test(after)) kind = 'namespace'
        else kind = vocab.methods.has(name) ? 'function' : 'unknown'
      } else if (name === ROOT) kind = 'namespace'
      else if (/^\s*=(?!=)/u.test(after)) kind = 'punct' // 關鍵字參數的名稱（strict=、literal=…）
      else kind = 'unknown'
      push(kind, name)
      i += name.length
      continue
    }
    const op = OPERATORS.find((o) => text.startsWith(o, i))
    if (op) {
      push('operator', op)
      i += op.length
      continue
    }
    if (PUNCT.includes(ch)) {
      push('punct', ch)
      i += 1
      continue
    }
    // 認不得的字元（例如全形的括號、引號）：一個字一段（表情符號這類兩個 UTF-16 的字也一起）
    const point = String.fromCodePoint(text.codePointAt(i)!)
    push('error', point)
    i += point.length
  }
  return tokens
}

/** 插入一段文字：取代 start～end（選取的部分），游標放在插入的文字裡的 caret 位置。 */
export function insertAt(
  text: string,
  start: number,
  end: number,
  snippet: string,
  caret: number = snippet.length,
): { text: string; cursor: number } {
  const from = Math.max(0, Math.min(start, text.length))
  const to = Math.max(from, Math.min(end, text.length))
  return { text: text.slice(0, from) + snippet + text.slice(to), cursor: from + Math.max(0, Math.min(caret, snippet.length)) }
}
