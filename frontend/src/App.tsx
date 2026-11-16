import {
  Bot,
  ChevronDown,
  ChevronUp,
  CircleCheck,
  Columns3,
  Download,
  FilePlus2,
  FolderOpen,
  MoreHorizontal,
  Play,
  Redo2,
  Save,
  Stethoscope,
  TriangleAlert,
  Undo2,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { usePanelRef } from 'react-resizable-panels'

import iconUrl from '../../assets/icon.svg'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import {
  onProgress,
  waitForApi,
  type Issue,
  type Progress,
  type ReadyInfo,
  type Result,
  type SelftestResult,
  type TabladeApi,
  type UpdateInfo,
  type ViewState,
} from './bridge'
import { StepForm } from './components/form/StepForm'
import { PreviewGrid, type AddStepRequest, type GridHandle } from './components/PreviewGrid'
import { CollapseButton, CollapsiblePane, HEADER_HEIGHT, PaneHeader, PaneMeta, PaneTitle, RAIL_WIDTH } from './components/Pane'
import { RecipeTitle } from './components/RecipeTitle'
import { SelftestDialog } from './components/SelftestDialog'
import { ReportDialog } from './components/ReportDialog'
import { InputPanel, OutputPanel } from './components/SourcePanel'
import { StepsPanel } from './components/StepsPanel'
import { UpdateNotice } from './components/UpdateNotice'

type Status = { text: string; error?: boolean }
const SMOKE_TIMEOUT_MS = 30_000

function formatBytes(n: number) {
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(0)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}

/** 讀檔、轉碼、匯出時的進度（大檔可能要好幾分鐘）。 */
function ProgressBar({ progress }: { progress: Progress }) {
  const ratio = progress.total > 0 ? Math.min(progress.done / progress.total, 1) : null
  return (
    <div className="border-b bg-primary/5 px-3 py-1.5 text-xs">
      <div className="flex items-center gap-2">
        <span className="font-medium">{progress.stage}</span>
        <span className="min-w-0 flex-1 truncate text-muted-foreground">{progress.file}</span>
        {ratio !== null ? (
          <span className="tabular-nums text-muted-foreground">
            {formatBytes(progress.done)} / {formatBytes(progress.total)}（{Math.round(ratio * 100)}%）
          </span>
        ) : (
          <span className="text-muted-foreground">處理中…</span>
        )}
      </div>
      <div className="mt-1 h-1 overflow-hidden rounded bg-primary/15">
        <div
          className={`h-full bg-primary transition-[width] ${ratio === null ? 'w-1/3 animate-pulse' : ''}`}
          style={ratio === null ? undefined : { width: `${ratio * 100}%` }}
        />
      </div>
    </div>
  )
}

function ToolButton({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string
  onClick: () => void
  disabled?: boolean
  children: React.ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label={label} onClick={onClick} disabled={disabled}>
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

export default function App() {
  const [api, setApi] = useState<TabladeApi | null>(null)
  const [info, setInfo] = useState<ReadyInfo | null>(null)
  const [state, setState] = useState<ViewState | null>(null)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<Status>({ text: '載入中…' })
  const [showSystem, setShowSystem] = useState(false)
  const [report, setReport] = useState<{ open: boolean; issues: Issue[]; text: string }>({ open: false, issues: [], text: '' })
  const [selftest, setSelftest] = useState<SelftestResult | null>(null)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [update, setUpdate] = useState<UpdateInfo | null | undefined>(undefined)
  const grid = useRef<GridHandle>(null)
  const leftPanel = usePanelRef()
  const rightPanel = usePanelRef()
  const warnPanel = usePanelRef()

  const gridLoaded = useRef<((total: number) => void) | null>(null)

  /** 呼叫 Python：統一處理忙碌狀態、錯誤與新的畫面狀態。 */
  const call = useCallback(async <T,>(label: string, fn: () => Promise<Result<T>>): Promise<(T & { ok: true }) | null> => {
    setBusy(true)
    try {
      const res = await fn()
      if (!res.ok) {
        setStatus({ text: `${label}失敗：${res.error}`, error: true })
        if (res.issues?.length) setReport({ open: true, issues: res.issues, text: res.error })
        return null
      }
      const withState = res as { state?: ViewState }
      if (withState.state) setState(withState.state)
      return res as T & { ok: true }
    } catch (err) {
      setStatus({ text: `${label}失敗：${String(err)}`, error: true })
      return null
    } finally {
      setBusy(false)
    }
  }, [])

  const runSmoke = useCallback(async (target: TabladeApi): Promise<boolean> => {
    const st = await target.selftest()
    if (!st.ok || !st.result.ok) return false
    setSelftest(st.result)
    const loaded = new Promise<number>((resolve) => {
      gridLoaded.current = resolve
      window.setTimeout(() => resolve(-1), SMOKE_TIMEOUT_MS)
    })
    const ex = await target.smoke_example()
    if (!ex.ok) return false
    setState(ex.state)
    const steps = ex.state.steps
    const pipelineOk = steps.length === 8 && steps.every((s) => s.status === 'ok') && steps[7].rows === 70
    return pipelineOk && (await loaded) === 70
  }, [])

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
      onProgress(setProgress)
      const first = await target.state()
      if (first.ok) setState(first.state)
      setStatus({ text: '就緒。先新增或開啟 Recipe。' })
      // 啟動時查 GitHub 上的最新版號（背景進行，不擋畫面；不自動更新）
      void target.check_update().then((res) => setUpdate(res.ok ? res.update : null))
      if (ready.smoke) await target.smoke_done(await runSmoke(target))
    })()
    return () => {
      cancelled = true
    }
  }, [runSmoke])

  // ── 動作 ──────────────────────────────────────────────────

  const act = {
    newRecipe: async () => {
      if (!api) return
      const res = await call('新增 Recipe', () => api.new_recipe())
      if (res && !res.cancelled) setStatus({ text: `已建立 ${res.path}。接著在「讀取資料」加入檔案。` })
    },
    openRecipe: async () => {
      if (!api) return
      const res = await call('開啟 Recipe', () => api.open_recipe())
      if (!res || res.cancelled) return
      const next = res.state.input.can_load
        ? '按「載入資料」讀取檔案。'
        : res.state.input.files.length
          ? '檔案清單有找不到的檔，請先在「讀取資料」移除。'
          : '接著在「讀取資料」加入檔案。'
      setStatus({ text: `已開啟 ${res.state.recipe.path}。${next}` })
      if (res.issues?.length) await showReport(res.issues)
    },
    save: async (as: boolean) => {
      if (!api) return
      const res = await call('儲存', () => (as ? api.save_recipe_as() : api.save_recipe()))
      if (res && !res.cancelled) setStatus({ text: `已儲存 ${res.path}` })
    },
    addFiles: async () => {
      if (!api) return
      const res = await call('加入檔案', () => api.add_files())
      if (res && !res.cancelled) setStatus({ text: `檔案清單共 ${res.state.input.files.length} 項。按「載入資料」讀取。` })
    },
    loadData: async () => {
      if (!api) return
      const res = await call('載入資料', () => api.load_data())
      if (res) {
        const input = res.state.input
        setStatus({
          text: `已載入 ${input.sources.length} 個檔、${input.total_rows?.toLocaleString()} 行${input.sampled ? '（預覽用樣本）' : ''}`,
        })
      }
    },
    exportParquet: async () => {
      if (!api) return
      const res = await call('處理並匯出資料', () => api.export_parquet())
      if (res && !res.cancelled) {
        const warn = res.warnings ? `，警告 ${res.warnings.toLocaleString()} 則` : ''
        const how = res.mode === 'stream' ? '（串流處理全部資料）' : ''
        setStatus({ text: `已匯出 ${res.path}：${res.rows?.toLocaleString()} 列 × ${res.columns} 欄${warn}${how}` })
      }
    },
    downloadSkill: async () => {
      if (!api) return
      const res = await call('下載 Copilot skill', () => api.download_skill())
      if (res && !res.cancelled) setStatus({ text: `已存成 ${res.path}（Recipe 版本 ${res.version}）` })
    },
    selftest: async () => {
      if (!api) return
      const res = await call('自我檢測', () => api.selftest())
      if (res) setSelftest(res.result)
    },
    undo: () => api && call('復原', () => api.undo()),
    redo: () => api && call('重做', () => api.redo()),
  }

  const showReport = async (issues?: Issue[]) => {
    if (!api) return
    const res = await call('產生錯誤報告', () => api.error_report())
    if (res) setReport({ open: true, issues: issues ?? state?.issues ?? [], text: res.text })
  }

  const addStep = async (type: string, context: AddStepRequest['context'] = {}) => {
    if (!api) return
    const res = await call('新增步驟', () => api.add_step(type, context))
    if (res) setStatus({ text: `已新增：${res.state.steps[res.index]?.label}` })
  }

  // 快捷鍵（輸入框裡的 ⌘Z 交給瀏覽器處理）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey
      const typing = (e.target as HTMLElement).closest('input, textarea, [contenteditable]')
      if (!mod || !api) return
      const key = e.key.toLowerCase()
      const open = state?.recipe.open ?? false
      if (key === 's' && open) {
        e.preventDefault()
        void act.save(e.shiftKey)
      } else if (key === 'o') {
        e.preventDefault()
        void act.openRecipe()
      } else if (key === 'z' && !typing && open) {
        e.preventDefault()
        void (e.shiftKey ? act.redo() : act.undo())
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const selected = state && state.selected >= 0 ? (state.steps[state.selected] ?? null) : null
  const catalogItem = selected ? info?.catalog.find((c) => c.type === selected.type) : undefined

  // 預覽的內容變了才讓表格重新載入
  const previewKey = useMemo(() => {
    if (!state) return ''
    const upto = state.steps.slice(0, state.preview.step + 1).map((s) => s.params)
    return JSON.stringify([
      state.preview.step,
      state.input.files,
      state.input.loaded,
      state.input.reader,
      state.input.encoding,
      upto,
      state.preview.rows,
    ])
  }, [state])

  const onGridLoaded = useCallback((total: number) => {
    gridLoaded.current?.(total)
    gridLoaded.current = null
  }, [])

  if (!state || !info || !api) {
    return <div className="flex h-svh items-center justify-center text-sm text-muted-foreground">{status.text}</div>
  }

  const footer = (
    <footer className="flex items-center gap-3 border-t px-3 py-1 text-xs">
      <span role="status" className={`min-w-0 flex-1 truncate ${status.error ? 'text-destructive' : 'text-muted-foreground'}`}>
        {status.text}
      </span>
      <span className="shrink-0 text-muted-foreground">
        Tablade {info.version}・Recipe {info.recipe_version}
      </span>
    </footer>
  )
  const dialogs = (
    <>
      <ReportDialog
        open={report.open}
        onOpenChange={(open) => setReport((r) => ({ ...r, open }))}
        issues={report.issues}
        report={report.text}
      />
      <SelftestDialog result={info.smoke ? null : selftest} onClose={() => setSelftest(null)} />
    </>
  )

  if (!state.recipe.open) {
    return (
      <TooltipProvider>
        <div className="flex h-svh flex-col bg-background">
          <header className="flex items-center gap-2 border-b px-3 py-1.5">
            <img src={iconUrl} alt="Tablade" className="size-6" />
            <span className="px-1 text-[15px] font-semibold">Tablade</span>
            <div className="ml-auto flex items-center gap-1">
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon-sm" aria-label="更多">
                    <MoreHorizontal />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-60 whitespace-nowrap">
                  <DropdownMenuItem onSelect={() => void act.downloadSkill()}>
                    <Download /> 下載 Copilot skill（v{info.recipe_version}）
                  </DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => void act.selftest()}>
                    <Stethoscope /> 自我檢測
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </header>
          <main className="flex min-h-0 flex-1 items-center justify-center p-6">
            <div className="w-full max-w-md space-y-4 rounded-lg border p-6 text-center">
              <img src={iconUrl} alt="" className="mx-auto size-12" />
              <h1 className="text-lg font-semibold">開始整理資料</h1>
              <p className="text-sm text-muted-foreground">
                先新增一個 Recipe（會請你選擇存放位置並命名），或開啟已經存好的 Recipe。之後在「讀取資料」加入檔案，再按「載入資料」。
              </p>
              <div className="flex justify-center gap-2">
                <Button onClick={() => void act.newRecipe()} disabled={busy}>
                  <FilePlus2 /> 新增 Recipe
                </Button>
                <Button variant="outline" onClick={() => void act.openRecipe()} disabled={busy}>
                  <FolderOpen /> 開啟 Recipe
                </Button>
              </div>
              <UpdateNotice current={info.version} update={update} />
            </div>
          </main>
          {footer}
        </div>
        {dialogs}
      </TooltipProvider>
    )
  }

  const input = state.input
  const emptyPreview = !input.files.length
    ? '先在「讀取資料」按「瀏覽檔案…」加入檔案。'
    : !input.can_load
      ? '檔案清單有找不到的檔，請先在「讀取資料」移除或修正。'
      : input.stale
        ? '檔案清單、讀取方式或編碼已變更，請在「讀取資料」按「載入資料」重新讀取。'
        : '在「讀取資料」按「載入資料」讀取檔案。'

  const outputSelected = state.selected === state.steps.length
  const warnings = state.preview.warnings
  const previewLabel = state.selected < 0 ? '讀取資料' : outputSelected ? '輸出資料' : `步驟 ${state.selected + 1}`
  const errorCount = state.steps.filter((s) => s.status === 'error').length

  return (
    <TooltipProvider>
      <div className="flex h-svh flex-col bg-background">
        <header className="flex flex-wrap items-center gap-1 border-b px-3 py-1.5">
          <img src={iconUrl} alt="Tablade" className="size-6" />
          <RecipeTitle
            name={state.recipe.name}
            path={state.recipe.path}
            onCommit={(name) => void call('更新名稱', () => api.set_meta({ name }))}
          />
          <div className="mx-1 h-5 w-px bg-border" />
          <ToolButton label="新增 Recipe" onClick={() => void act.newRecipe()} disabled={busy}>
            <FilePlus2 />
          </ToolButton>
          <ToolButton label="開啟 Recipe（⌘O）" onClick={() => void act.openRecipe()} disabled={busy}>
            <FolderOpen />
          </ToolButton>
          <ToolButton label="儲存 Recipe（⌘S，⇧⌘S 另存）" onClick={() => void act.save(false)} disabled={busy}>
            <Save />
          </ToolButton>
          <div className="mx-1 h-5 w-px bg-border" />
          <ToolButton label="復原（⌘Z）" onClick={() => void act.undo()} disabled={busy || !state.can_undo}>
            <Undo2 />
          </ToolButton>
          <ToolButton label="重做（⇧⌘Z）" onClick={() => void act.redo()} disabled={busy || !state.can_redo}>
            <Redo2 />
          </ToolButton>
          <div className="mx-1 h-5 w-px bg-border" />
          <Tooltip>
            <TooltipTrigger asChild>
              <span>
                <Button size="sm" onClick={() => void act.exportParquet()} disabled={busy || errorCount > 0 || !input.loaded}>
                  <Play /> 處理並匯出資料
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent>
              {!input.loaded
                ? '先在「讀取資料」按「載入資料」'
                : errorCount > 0
                  ? '有步驟出錯，請先修正'
                  : '處理全部資料並存成 Parquet（按下後選擇存檔位置）'}
            </TooltipContent>
          </Tooltip>
          {state.recipe.dirty ? <span className="ml-1.5 text-xs text-muted-foreground">尚未儲存</span> : null}
          <div className="ml-auto flex items-center gap-1">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm">
                  <Bot /> Copilot
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-60 whitespace-nowrap">
                <DropdownMenuItem onSelect={() => void showReport()}>
                  <TriangleAlert /> 檢查並複製錯誤報告
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => void act.downloadSkill()}>
                  <Download /> 下載 Copilot skill（v{info.recipe_version}）
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon-sm" aria-label="更多">
                  <MoreHorizontal />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-48 whitespace-nowrap">
                <DropdownMenuItem onSelect={() => void act.save(true)}>
                  <Save /> 另存 Recipe…
                </DropdownMenuItem>
                <DropdownMenuCheckboxItem checked={showSystem} onCheckedChange={(v) => setShowSystem(v === true)}>
                  <Columns3 /> 顯示系統欄位
                </DropdownMenuCheckboxItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => void act.selftest()}>
                  <Stethoscope /> 自我檢測
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>
        {busy && progress ? <ProgressBar progress={progress} /> : null}

        <ResizablePanelGroup orientation="horizontal" className="min-h-0 flex-1">
          <ResizablePanel defaultSize="22" minSize={220} collapsible collapsedSize={RAIL_WIDTH} panelRef={leftPanel}>
            <CollapsiblePane side="left" label="步驟" onExpand={() => leftPanel.current?.expand()}>
              <StepsPanel
                state={state}
                catalog={info.catalog}
                busy={busy}
                onSelect={(i) => void call('選取', () => api.select_step(i))}
                onAdd={(type) => void addStep(type)}
                onMove={(i, to) => void call('移動步驟', () => api.move_step(i, to))}
                onToggle={(i) => void call('切換步驟', () => api.toggle_step(i))}
                onDelete={(i) => void call('刪除步驟', () => api.delete_step(i))}
                onCollapse={() => leftPanel.current?.collapse()}
              />
            </CollapsiblePane>
          </ResizablePanel>
          <ResizableHandle />
          <ResizablePanel defaultSize="53" minSize="30">
            <ResizablePanelGroup orientation="vertical">
              <ResizablePanel defaultSize="78" minSize="30">
                <div className="flex h-full flex-col">
                  <PaneHeader>
                    <PaneTitle>資料</PaneTitle>
                    <PaneMeta>
                      {previewLabel}
                      {state.preview.rows !== null
                        ? `・${state.preview.rows.toLocaleString()} 列 × ${state.preview.columns.filter((c) => !c.system).length} 欄`
                        : ''}
                    </PaneMeta>
                    {state.input.sampled ? (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Badge variant="outline" className="border-amber-500/50 text-amber-700 dark:text-amber-400">
                            預覽樣本
                          </Badge>
                        </TooltipTrigger>
                        <TooltipContent className="max-w-80">
                          資料共 {state.input.total_rows?.toLocaleString()} 行，預覽只用每個檔案的前{' '}
                          {state.input.sample.head.toLocaleString()} 行與最後 {state.input.sample.tail.toLocaleString()} 行，
                          步驟的列數也是樣本的。匯出時會處理全部資料。
                        </TooltipContent>
                      </Tooltip>
                    ) : null}
                    {state.preview.step !== state.preview.requested ? (
                      <Badge variant="destructive">
                        {outputSelected ? '最後一步' : '這一步'}
                        還算不出來，下面是
                        {state.preview.step < 0 ? '讀取的資料' : `第 ${state.preview.step + 1} 步的結果`}
                      </Badge>
                    ) : null}
                  </PaneHeader>
                  <div className="min-h-0 flex-1">
                    {input.loaded ? (
                      <PreviewGrid
                        ref={grid}
                        api={api}
                        preview={state.preview}
                        resetKey={previewKey}
                        showSystem={showSystem}
                        maxLine={state.input.total_rows}
                        onAddStep={(r) => void addStep(r.type, r.context)}
                        onLoaded={onGridLoaded}
                      />
                    ) : (
                      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center text-sm text-muted-foreground">
                        <p>{emptyPreview}</p>
                        {state.selected !== -1 ? (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => {
                              rightPanel.current?.expand()
                              void call('選取', () => api.select_step(-1))
                            }}
                          >
                            前往「讀取資料」
                          </Button>
                        ) : null}
                      </div>
                    )}
                  </div>
                </div>
              </ResizablePanel>
              <ResizableHandle />
              <ResizablePanel defaultSize="22" minSize={90} collapsible collapsedSize={HEADER_HEIGHT} panelRef={warnPanel}>
                {/* 收合到只剩標題列時，右邊的按鈕改成「展開」（index.css 的 .warn-pane） */}
                <div className="warn-pane flex h-full flex-col">
                  <PaneHeader className={warnings.length ? 'bg-amber-500/10' : ''}>
                    {warnings.length ? (
                      <TriangleAlert className="size-4 shrink-0 text-amber-600 dark:text-amber-400" />
                    ) : (
                      <CircleCheck className="size-4 shrink-0 text-green-600" />
                    )}
                    <span
                      className={`text-sm font-medium ${warnings.length ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'}`}
                    >
                      {warnings.length ? `有警告（${warnings.length.toLocaleString()}）` : '沒有警告'}
                    </span>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      className="ml-auto"
                      aria-label="收合或展開警告"
                      title="收合或展開警告"
                      onClick={() => (warnPanel.current?.isCollapsed() ? warnPanel.current.expand() : warnPanel.current?.collapse())}
                    >
                      <ChevronDown className="warn-collapse" />
                      <ChevronUp className="warn-expand" />
                    </Button>
                  </PaneHeader>
                  <ul className="flex-1 overflow-auto text-sm">
                    {warnings.map((w, i) => (
                      <li key={i}>
                        <button
                          type="button"
                          className="flex w-full gap-2 px-3 py-1 text-left hover:bg-muted"
                          onClick={async () => {
                            if (w.source_file === null || w.line_no === null) return
                            const res = await api.find_row(state.preview.step, w.source_file, w.line_no)
                            if (res.ok && res.row !== null) grid.current?.scrollToRow(res.row)
                          }}
                        >
                          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-amber-600" />
                          <span className="shrink-0 font-mono text-xs text-muted-foreground">
                            {w.source_file}:{w.line_no}
                          </span>
                          <span>{w.message}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              </ResizablePanel>
            </ResizablePanelGroup>
          </ResizablePanel>
          <ResizableHandle />
          <ResizablePanel defaultSize="25" minSize={280} collapsible collapsedSize={RAIL_WIDTH} panelRef={rightPanel}>
            <CollapsiblePane side="right" label="設定" onExpand={() => rightPanel.current?.expand()}>
              <div className="flex h-full flex-col">
                <PaneHeader>
                  <CollapseButton side="right" label="設定" onClick={() => rightPanel.current?.collapse()} />
                  <PaneTitle>設定</PaneTitle>
                </PaneHeader>
                <div className="min-h-0 flex-1 overflow-auto p-4">
                  {selected && catalogItem ? (
                    <StepForm
                      key={selected.id}
                      step={selected}
                      item={catalogItem}
                      onChange={(values) => void call('更新步驟', () => api.update_step(selected.index, values))}
                    />
                  ) : outputSelected ? (
                    <OutputPanel state={state} onOutput={(v) => void call('更新輸出設定', () => api.set_output(v))} />
                  ) : (
                    <InputPanel
                      state={state}
                      busy={busy}
                      onLoad={() => void act.loadData()}
                      onAddFiles={() => void act.addFiles()}
                      onAddEntry={(text) => void call('加入路徑', () => api.add_file_entry(text))}
                      onRemove={(i) => void call('移除檔案', () => api.remove_file(i))}
                      onInput={(v) => void call('更新資料設定', () => api.set_input(v))}
                    />
                  )}
                </div>
              </div>
            </CollapsiblePane>
          </ResizablePanel>
        </ResizablePanelGroup>

        {footer}
      </div>

      {dialogs}
    </TooltipProvider>
  )
}
