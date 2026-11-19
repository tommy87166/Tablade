/** 運算式的語法高亮：切好的每一段畫成有顏色的 span（自訂的編輯框與語法選擇器共用）。 */
import type { Token, TokenKind } from '@/lib/expression'

const COLORS: Record<TokenKind, string> = {
  function: 'text-primary',
  namespace: 'text-primary',
  unknown: 'text-destructive underline decoration-wavy decoration-destructive/70',
  column: 'text-expr-column',
  error: 'text-destructive underline decoration-wavy decoration-destructive/70',
  string: 'text-expr-string',
  number: 'text-expr-number',
  keyword: 'text-expr-keyword',
  comment: 'text-muted-foreground italic',
  operator: 'text-foreground',
  punct: 'text-foreground',
  space: '',
}

/** 切好的每一段畫成有顏色的 span（React 的元素，不直接寫 HTML） */
export function Highlighted({ tokens }: { tokens: Token[] }) {
  return (
    <>
      {tokens.map((token, i) => (
        <span key={i} className={COLORS[token.kind]}>
          {token.text}
        </span>
      ))}
    </>
  )
}
