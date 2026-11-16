/**
 * 標題列上的 recipe 名稱：直接點了就能改（Enter 或離開輸入框時送出，Esc 取消）。
 * 游標在輸入框裡時不讓 Python 的值蓋掉正在打的字；其他時候一律以 Python 的值為準（新增、開啟、undo）。
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
  onCommit: (name: string) => void
}

export function RecipeTitle({ name, path, onCommit }: Props) {
  const [draft, setDraft] = useState(name)
  const [seen, setSeen] = useState(name)
  const [editing, setEditing] = useState(false)
  if (name !== seen) {
    setSeen(name)
    if (!editing) setDraft(name)
  }

  const commit = () => {
    setEditing(false)
    const next = draft.trim()
    setDraft(next)
    if (next !== name) onCommit(next)
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
            onFocus={() => setEditing(true)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') e.currentTarget.blur()
              if (e.key === 'Escape') {
                setDraft(name)
                window.setTimeout(() => (e.target as HTMLInputElement).blur())
              }
            }}
          />
        </TooltipTrigger>
        <TooltipContent>{path ?? '尚未存檔'}</TooltipContent>
      </Tooltip>
    </div>
  )
}
