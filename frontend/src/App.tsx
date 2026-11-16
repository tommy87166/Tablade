import { useCallback, useEffect, useState } from 'react'
import { CircleCheck, CircleX, FileDown, FileUp, Stethoscope } from 'lucide-react'

import iconUrl from '../../assets/icon.svg'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { waitForApi, type CsvPreview, type ReadyInfo, type Result, type SelftestResult, type TabladeApi } from './bridge'

type Status = { text: string; error?: boolean }

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export default function App() {
  const [api, setApi] = useState<TabladeApi | null>(null)
  const [info, setInfo] = useState<ReadyInfo | null>(null)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<Status>({ text: '載入中…' })
  const [selftest, setSelftest] = useState<SelftestResult | null>(null)
  const [preview, setPreview] = useState<CsvPreview | null>(null)

  /** 呼叫 Python，統一處理忙碌狀態與錯誤；失敗時回傳 null。 */
  const call = useCallback(async <T,>(label: string, fn: () => Promise<Result<T>>) => {
    setBusy(true)
    setStatus({ text: `${label}…` })
    try {
      const res = await fn()
      if (!res.ok) {
        setStatus({ text: `${label}失敗：${res.error}`, error: true })
        return null
      }
      return res
    } catch (err) {
      setStatus({ text: `${label}失敗：${String(err)}`, error: true })
      return null
    } finally {
      setBusy(false)
    }
  }, [])

  const runSelftest = useCallback(
    async (target: TabladeApi) => {
      const res = await call('自我檢測', () => target.selftest())
      if (!res) return false
      setSelftest(res.result)
      setStatus({ text: res.result.ok ? '自我檢測通過' : '自我檢測失敗', error: !res.result.ok })
      return res.result.ok
    },
    [call],
  )

  useEffect(() => {
    let cancelled = false
    void (async () => {
      const target = await waitForApi()
      const ready = await target.ready()
      if (cancelled) return
      if (!ready.ok) {
        setStatus({ text: `初始化失敗：${ready.error}`, error: true })
        return
      }
      setApi(target)
      setInfo(ready)
      setStatus({ text: '就緒。可以先按「自我檢測」確認環境。' })
      // --smoke：自動跑自我檢測、回報結果後由 Python 關閉視窗
      if (ready.smoke) await target.smoke_done(await runSelftest(target))
    })()
    return () => {
      cancelled = true
    }
  }, [runSelftest])

  const openCsv = async () => {
    if (!api) return
    const res = await call('讀取 CSV', () => api.open_csv())
    if (!res) return
    if (res.cancelled) return setStatus({ text: '已取消' })
    setPreview(res)
    setStatus({ text: `已讀取 ${res.file}` })
  }

  const exportParquet = async () => {
    if (!api) return
    const res = await call('匯出 Parquet', () => api.export_parquet())
    if (!res) return
    if (res.cancelled) return setStatus({ text: '已取消' })
    setStatus({ text: `已匯出 ${res.file}：${res.rows.toLocaleString()} 列，${formatBytes(res.bytes)}` })
  }

  const disabled = !api || busy

  return (
    <div className="flex h-svh flex-col bg-muted/40">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b bg-background px-5 py-3">
        <div className="flex items-center gap-2.5 whitespace-nowrap">
          <img src={iconUrl} alt="" className="size-7" />
          <span className="text-lg font-semibold tracking-wide">Tablade</span>
          <Badge variant="secondary">M0 環境驗證</Badge>
        </div>
        <div className="flex gap-2">
          <Button onClick={openCsv} disabled={disabled}>
            <FileUp /> 開啟 CSV
          </Button>
          <Button onClick={exportParquet} disabled={disabled || !preview}>
            <FileDown /> 匯出 Parquet
          </Button>
          <Button variant="outline" onClick={() => api && runSelftest(api)} disabled={disabled}>
            <Stethoscope /> 自我檢測
          </Button>
        </div>
      </header>

      <main className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-5">
        {status.error ? (
          <Alert variant="destructive">
            <CircleX />
            <AlertDescription>{status.text}</AlertDescription>
          </Alert>
        ) : (
          <p role="status" className="text-sm text-muted-foreground">
            {status.text}
          </p>
        )}

        {selftest && (
          <Card size="sm" className="shrink-0">
            <CardHeader>
              <CardTitle>自我檢測</CardTitle>
              <CardDescription>
                {Object.entries(selftest.versions)
                  .map(([k, v]) => `${k} ${v}`)
                  .join('　')}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ul className="space-y-1">
                {selftest.checks.map((c) => (
                  <li key={c.name} className="flex items-start gap-2">
                    {c.ok ? (
                      <CircleCheck className="mt-0.5 size-4 shrink-0 text-green-600" />
                    ) : (
                      <CircleX className="mt-0.5 size-4 shrink-0 text-destructive" />
                    )}
                    <span>
                      {c.name}：<span className="text-muted-foreground">{c.detail}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}

        {preview && (
          <Card size="sm" className="shrink-0">
            <CardHeader>
              <CardTitle>預覽：{preview.file}</CardTitle>
              <CardDescription>
                {[
                  `${preview.rows.toLocaleString()} 列 × ${preview.columns.length} 欄`,
                  `編碼 ${preview.encoding}`,
                  formatBytes(preview.bytes),
                  `顯示前 ${preview.preview.length} 列`,
                ].join('　')}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="max-h-[60vh] overflow-auto rounded-md border">
                <Table className="tabular-nums">
                  <TableHeader className="sticky top-0 bg-background">
                    <TableRow>
                      <TableHead className="text-right text-muted-foreground">#</TableHead>
                      {preview.columns.map((name) => (
                        <TableHead key={name}>{name}</TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {preview.preview.map((row, i) => (
                      <TableRow key={i}>
                        <TableCell className="text-right text-muted-foreground">{i + 1}</TableCell>
                        {row.map((value, j) =>
                          value === null ? (
                            <TableCell key={j} className="italic text-muted-foreground">
                              null
                            </TableCell>
                          ) : (
                            <TableCell key={j}>{String(value)}</TableCell>
                          ),
                        )}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
        )}
      </main>

      <footer className="border-t bg-background px-5 py-1.5 text-xs text-muted-foreground">
        {info ? `Tablade ${info.version}　Polars ${info.polars}　${info.platform}　完全離線執行` : ' '}
      </footer>
    </div>
  )
}
