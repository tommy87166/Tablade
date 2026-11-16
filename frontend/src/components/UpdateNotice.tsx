/** 起始頁的版本資訊：啟動時查到 GitHub 上有新版就提示下載網址（不自動更新）。 */
import { Check, Copy } from 'lucide-react'
import { useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import type { UpdateInfo } from '../bridge'

interface Props {
  current: string
  /** undefined：還在查；null：不檢查（smoke test） */
  update: UpdateInfo | null | undefined
}

export function UpdateNotice({ current, update }: Props) {
  const field = useRef<HTMLInputElement>(null)
  const [copied, setCopied] = useState(false)
  if (update === null) return null
  if (update === undefined) return <p className="text-xs text-muted-foreground">目前版本 v{current}・正在檢查更新…</p>
  if (!update.newer) {
    return (
      <p className="text-xs text-muted-foreground">
        目前版本 v{current}・{update.error ?? '已是最新版'}
      </p>
    )
  }
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(update.url)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      field.current?.select() // WebView 不一定能用剪貼簿，改成選取讓使用者自己複製
    }
  }
  return (
    <div className="space-y-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-left text-sm">
      <p className="font-medium text-amber-800 dark:text-amber-300">
        有新版本 v{update.latest}（目前 v{update.current}）
      </p>
      <p className="text-xs text-muted-foreground">請到下面的網址下載新版，Tablade 不會自動更新。</p>
      <div className="flex gap-1.5">
        <input
          ref={field}
          readOnly
          value={update.url}
          aria-label="新版下載網址"
          onFocus={(e) => e.currentTarget.select()}
          className="h-8 min-w-0 flex-1 rounded-md border bg-background px-2 font-mono text-xs"
        />
        <Button size="sm" variant="outline" onClick={() => void copy()}>
          {copied ? <Check /> : <Copy />} {copied ? '已複製' : '複製網址'}
        </Button>
      </div>
    </div>
  )
}
