/**
 * 自我檢測的結果：按下「自我檢測」時 Python 用假資料跑一次（在暫存資料夾，做完就刪除）。
 * 分四段：結果、版本及環境、每一項檢查、暫存資料夾（位置、大小、用檔案總管／Finder 打開）。
 */
import { CircleCheck, CircleMinus, CircleX, FolderOpen } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import type { SelftestResult, TabladeApi } from '../bridge'
import { formatBytes } from '../lib/format'

/** 版本資訊的顯示名稱與說明。 */
const VERSIONS: Record<string, [string, string]> = {
  tablade: ['Tablade', ''],
  python: ['Python', 'App 內建'],
  polars: ['Polars', '資料處理引擎'],
  fastexcel: ['fastexcel', '讀 Excel 的引擎'],
  pydantic: ['pydantic', '檢查步驟參數'],
  platform: ['作業系統', ''],
  frozen: ['執行方式', ''],
}

function versionValue(key: string, value: string) {
  if (key === 'frozen') return value === 'True' ? '打包好的 App' : '原始碼（開發用）'
  return value
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  )
}

interface Props {
  result: SelftestResult | null
  api: TabladeApi | null
  onClose: () => void
}

/** 暫存資料夾：位置、目前大小、［開啟暫存資料夾］。 */
function StagingFolder({ api }: { api: TabladeApi }) {
  const [info, setInfo] = useState<{ path: string; bytes: number } | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let cancelled = false
    void api.staging_info().then((res) => {
      if (cancelled) return
      if (res.ok) setInfo({ path: res.path, bytes: res.bytes })
      else setError(res.error)
    })
    return () => {
      cancelled = true
    }
  }, [api])
  const open = async () => {
    const res = await api.open_staging()
    if (res.ok) setInfo({ path: res.path, bytes: res.bytes })
    else setError(res.error)
  }
  return (
    <Section title="暫存資料夾">
      <p className="text-xs text-muted-foreground">
        轉碼後的檔與處理中的暫存檔放在這裡，關掉 App 時會刪除；上次沒正常關掉留下的，下次開啟時清掉。
      </p>
      {info ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <code className="min-w-0 flex-1 rounded bg-muted px-2 py-1 text-xs break-all">{info.path}</code>
          <span className="text-xs text-muted-foreground tabular-nums">目前 {formatBytes(info.bytes)}</span>
          <Button size="sm" variant="outline" onClick={() => void open()}>
            <FolderOpen /> 開啟暫存資料夾
          </Button>
        </div>
      ) : null}
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </Section>
  )
}

export function SelftestDialog({ result, api, onClose }: Props) {
  const checks = result?.checks ?? []
  const failed = checks.find((c) => !c.ok && c.ran !== false)
  return (
    <Dialog open={result !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>自我檢測</DialogTitle>
          <DialogDescription>
            按下「自我檢測」時自動執行：用假資料測試這台電腦能不能正常執行 Tablade。在暫存資料夾進行，做完就刪除，不會碰你的檔案。
          </DialogDescription>
        </DialogHeader>

        <Section title="結果">
          <div
            className={`flex items-center gap-2 rounded-md border p-3 ${
              result?.ok ? 'border-green-600/30 bg-green-600/10' : 'border-destructive/30 bg-destructive/10'
            }`}
          >
            {result?.ok ? (
              <CircleCheck className="size-5 shrink-0 text-green-600" />
            ) : (
              <CircleX className="size-5 shrink-0 text-destructive" />
            )}
            <span className="font-semibold">{result?.ok ? '通過' : '失敗'}</span>
            <span className="text-sm text-muted-foreground">
              {result?.ok ? `${checks.length} 項檢查全部通過` : failed ? `「${failed.name}」失敗，請看下方的錯誤原因` : ''}
            </span>
          </div>
        </Section>

        <Section title="版本及環境">
          <dl className="grid grid-cols-[6rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
            {Object.entries(result?.versions ?? {}).map(([key, value]) => {
              const [label, note] = VERSIONS[key] ?? [key, '']
              return (
                <div key={key} className="contents">
                  <dt className="text-muted-foreground">{label}</dt>
                  <dd className="min-w-0 break-words">
                    {versionValue(key, value)}
                    {note ? <span className="ml-1.5 text-xs text-muted-foreground">{note}</span> : null}
                  </dd>
                </div>
              )
            })}
          </dl>
        </Section>

        <Section title="檢查">
          <ol className="space-y-2">
            {checks.map((c, i) => (
              <li key={c.name} className="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-x-2">
                {c.ok ? (
                  <CircleCheck className="mt-0.5 size-4 text-green-600" />
                ) : c.ran === false ? (
                  <CircleMinus className="mt-0.5 size-4 text-muted-foreground" />
                ) : (
                  <CircleX className="mt-0.5 size-4 text-destructive" />
                )}
                <div className="min-w-0">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
                    <span className="font-medium">
                      {i + 1}. {c.name}
                    </span>
                    <span className={c.ok || c.ran === false ? 'text-muted-foreground' : 'text-destructive'}>{c.detail}</span>
                  </div>
                  {c.purpose ? <div className="text-xs text-muted-foreground">確認：{c.purpose}</div> : null}
                </div>
              </li>
            ))}
          </ol>
        </Section>

        {api && result ? <StagingFolder api={api} /> : null}
      </DialogContent>
    </Dialog>
  )
}
