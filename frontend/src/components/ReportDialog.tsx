/**
 * 驗證結果與錯誤報告：開啟 recipe 有問題時自動顯示，也可以隨時從工具列打開。
 * 「複製錯誤報告」的內容是給 AI 助理（Copilot）看的，貼回去讓它修正 recipe（DESIGN.md §17.1）。
 */
import { Check, Copy } from 'lucide-react'
import { useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import type { Issue } from '../bridge'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  issues: Issue[]
  report: string
}

async function copyText(text: string, fallback: HTMLTextAreaElement | null): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // WebView 以字串載入的頁面不一定能用 Clipboard API，退回選取＋複製
    if (!fallback) return false
    fallback.focus()
    fallback.select()
    return document.execCommand('copy')
  }
}

export function ReportDialog({ open, onOpenChange, issues, report }: Props) {
  const area = useRef<HTMLTextAreaElement>(null)
  const [copied, setCopied] = useState(false)
  const errors = issues.filter((i) => i.severity === 'error')

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{errors.length ? `recipe 有 ${errors.length} 個問題` : '驗證結果'}</DialogTitle>
          <DialogDescription>
            把報告複製貼給 AI 助理（例如 Copilot），請它依報告修正 recipe；也可以直接在左側步驟裡修改。
          </DialogDescription>
        </DialogHeader>
        {issues.length ? (
          <ul className="max-h-48 space-y-1 overflow-auto rounded-md border p-2 text-sm">
            {issues.map((i) => (
              <li key={`${i.path}:${i.error}`}>
                <span className="font-mono text-xs text-muted-foreground">{i.path}</span>{' '}
                <span className={i.severity === 'error' ? 'text-destructive' : 'text-amber-700 dark:text-amber-400'}>{i.error}</span>
                {i.hint ? <span className="text-muted-foreground">（{i.hint}）</span> : null}
              </li>
            ))}
          </ul>
        ) : null}
        <Textarea ref={area} readOnly value={report} className="h-64 font-mono text-xs" />
        <DialogFooter>
          <Button
            onClick={async () => {
              setCopied(await copyText(report, area.current))
              window.setTimeout(() => setCopied(false), 2000)
            }}
          >
            {copied ? <Check /> : <Copy />} {copied ? '已複製' : '複製錯誤報告'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
