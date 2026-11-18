/**
 * 標題列上的 recipe 名稱：直接點了就能改（Enter 或離開輸入框時送出，Esc 取消）。
 * 游標在輸入框裡時不讓 Python 的值蓋掉正在打的字；其他時候一律以 Python 的值為準（新增、開啟、undo）。
 */
import { useLayoutEffect, useRef, useState } from 'react'

import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { tr } from '@/i18n'

let canvas: HTMLCanvasElement | null = null

/** 用輸入框實際的字型量文字寬度（大寫英文、數字、中文都準）。 */
function measure(text: string, el: HTMLElement): number {
  const ctx = (canvas ??= document.createElement('canvas')).getContext('2d')
  if (!ctx) return text.length * 15
  const style = getComputedStyle(el)
  ctx.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`
  return ctx.measureText(text).width
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

  const placeholder = tr('未命名 Recipe', 'Untitled recipe')
  const input = useRef<HTMLInputElement>(null)
  const [width, setWidth] = useState(120)
  useLayoutEffect(() => {
    if (input.current) setWidth(Math.min(Math.ceil(measure(draft || placeholder, input.current)) + 22, 420))
  }, [draft, placeholder])

  return (
    <div className="flex min-w-0 items-center">
      <Tooltip>
        <TooltipTrigger asChild>
          <input
            ref={input}
            aria-label={tr('Recipe 名稱', 'Recipe name')}
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
        <TooltipContent>{path ?? tr('尚未存檔', 'Not saved yet')}</TooltipContent>
      </Tooltip>
    </div>
  )
}
