/**
 * 標題列上的 recipe 名稱：直接點了就能改（Enter 或離開輸入框時送出，Esc 取消）。
 * Python 回傳的值如果就是自己剛送出的，不覆蓋草稿；其他來源的變更（undo、開啟 recipe）才更新。
 */
import { useState } from 'react'

import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

/** 估算文字寬度：中日韓字元約 15px，其他約 8px（15px 字型）。 */
function textWidth(text: string): number {
  let width = 0
  for (const ch of text) width += ch.codePointAt(0)! >= 0x2e80 ? 15 : 8
  return width
}

interface Props {
  name: string
  path: string | null
  dirty: boolean
  onCommit: (name: string) => void
}

export function RecipeTitle({ name, path, dirty, onCommit }: Props) {
  const [draft, setDraft] = useState(name)
  const [seen, setSeen] = useState(name)
  const [committed, setCommitted] = useState(name)
  if (name !== seen) {
    setSeen(name)
    if (name !== committed) setDraft(name)
  }

  const commit = () => {
    const next = draft.trim()
    setDraft(next)
    if (next !== committed) {
      setCommitted(next)
      onCommit(next)
    }
  }

  const placeholder = '未命名 Recipe'
  const width = Math.min(Math.max(textWidth(draft || placeholder) + 20, 80), 360)

  return (
    <div className="flex min-w-0 items-center">
      <Tooltip>
        <TooltipTrigger asChild>
          <input
            aria-label="Recipe 名稱"
            value={draft}
            placeholder={placeholder}
            spellCheck={false}
            style={{ width }}
            className="h-8 min-w-0 rounded-md border border-transparent bg-transparent px-2 text-[15px] font-semibold outline-none transition-colors placeholder:font-normal placeholder:text-muted-foreground hover:border-border focus:border-ring focus:bg-background"
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') e.currentTarget.blur()
              if (e.key === 'Escape') {
                setDraft(committed)
                window.setTimeout(() => (e.target as HTMLInputElement).blur())
              }
            }}
          />
        </TooltipTrigger>
        <TooltipContent>{path ?? '尚未存檔'}</TooltipContent>
      </Tooltip>
      {dirty ? (
        <span className="ml-0.5 text-muted-foreground" title="有尚未儲存的修改">
          •
        </span>
      ) : null}
    </div>
  )
}
