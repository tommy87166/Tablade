import {
  Bot,
  ChevronDown,
  ChevronUp,
  CircleCheck,
  Columns3,
  Download,
  FileInput,
  FilePlus2,
  FolderOpen,
  Globe,
  MoreHorizontal,
  Play,
  Redo2,
  Save,
  Shapes,
  Stethoscope,
  Trash2,
  TriangleAlert,
  Undo2,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { usePanelRef } from 'react-resizable-panels'

import iconUrl from '../../assets/icon.svg'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import {
  onProgress,
  onRun,
  onUsage,
  type RunState,
  type SampledStep,
  waitForApi,
  type Issue,
  type Progress,
  type ReadyInfo,
  type Result,
  type SelftestResult,
  type StepState,
  type TabladeApi,
  type UpdateInfo,
  type Usage,
  type ViewState,
} from './bridge'
import { FieldErrors } from './components/form/fields'
import { StepForm } from './components/form/StepForm'
import { PreviewGrid, type AddStepRequest, type GridHandle } from './components/PreviewGrid'
import { CollapseButton, CollapsiblePane, HEADER_HEIGHT, PaneHeader, PaneTitle, RAIL_WIDTH } from './components/Pane'
import { RecipeTitle } from './components/RecipeTitle'
import { SelftestDialog } from './components/SelftestDialog'
import { ReportDialog } from './components/ReportDialog'
import { RunDialog } from './components/RunDialog'
import { InputPanel, OutputPanel } from './components/SourcePanel'
import { StagesBar } from './components/StagesBar'
import { StepsPanel } from './components/StepsPanel'
import { TypesPane } from './components/TypesPane'
import { UpdateNotice } from './components/UpdateNotice'
import { UsageStatus } from './components/UsageStatus'
import { detectLang, lang, LANGS, plural, setLang, tr, type Lang } from './i18n'

type Status = { text: string; error?: boolean }

/** 錯誤訊息拿掉「SessionError: 」這類程式的錯誤類別（給人看的只要原因）。 */
const cleanError = (message: string) => message.replace(/^[A-Za-z]+Error: /, '')
/** 路徑只留檔名（狀態列放不下完整路徑；完整路徑在最上面 Recipe 名稱的提示裡）。 */
const fileName = (path: string | null | undefined) => (path ?? '').split(/[\\/]/).pop() ?? ''

/** 「第 16 步「移除重複」」；前面階段的步驟前面加「階段「文字明細」」 */
const sampledStepText = (s: SampledStep) =>
  s.stage
    ? tr(`階段「${s.stage}」第 ${s.number} 步「${s.label}」`, `Stage "${s.stage}", step ${s.number} "${s.label}"`)
    : tr(`第 ${s.number} 步「${s.label}」`, `Step ${s.number} "${s.label}"`)
const SMOKE_TIMEOUT_MS = 30_000
/** Excel 工作表最多幾列（CSV 超過時匯出摘要提醒） */
const EXCEL_MAX_ROWS = 1_048_576

/** 不支援的步驟類型（手寫或 AI 寫錯 type）：沒有表單可以改，顯示錯誤與刪除。 */
function UnknownStep({ step, busy, onDelete }: { step: StepState; busy: boolean; onDelete: () => void }) {
  return (
    <div className="space-y-3 text-sm">
      <p className="font-medium text-destructive">{tr('不支援的步驟類型', 'Unsupported step type')}</p>
      <p className="text-muted-foreground">
        {tr(
          `第 ${step.index + 1} 步的類型「${step.type ?? ''}」Tablade 不認得，沒有設定可以改。請修正 Recipe（例如請 AI 助理改成支援的步驟），或刪除這一步。`,
          `Tablade doesn't recognize the type "${step.type ?? ''}" of step ${step.index + 1}, so it has no settings to edit. Fix the Recipe (for example, ask the AI assistant to use a supported step) or delete this step.`,
        )}
      </p>
      <FieldErrors issues={step.errors} />
      <Button size="sm" variant="outline" disabled={busy} onClick={onDelete}>
        <Trash2 /> {tr('刪除這一步', 'Delete this step')}
      </Button>
    </div>
  )
}

/** 下方面板標題列的分頁按鈕（警告、型別）。 */
function BottomTab({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`flex items-center gap-1.5 rounded-md px-2 py-0.5 text-sm font-medium ${
        active ? 'bg-background text-foreground shadow-xs ring-1 ring-border' : 'text-muted-foreground hover:bg-muted'
      }`}
    >
      {children}
    </button>
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
  /** 最新的畫面狀態（排隊的指令真正送出時，用它找步驟現在的位置） */
  const stateRef = useRef<ViewState | null>(null)
  /** 排隊中與執行中的 Python 呼叫有幾個（兩個呼叫重疊時，先回來的不會把忙碌狀態清掉） */
  const [pendingCalls, setPendingCalls] = useState(0)
  const busy = pendingCalls > 0
  const [status, setStatus] = useState<Status>(() => ({ text: tr('載入中…', 'Loading…') }))
  const [showSystem, setShowSystem] = useState(false)
  const [bottomTab, setBottomTab] = useState<'warnings' | 'types'>('warnings')
  const [report, setReport] = useState<{ open: boolean; issues: Issue[]; text: string }>({ open: false, issues: [], text: '' })
  const [selftest, setSelftest] = useState<SelftestResult | null>(null)
  const [progress, setProgress] = useState<Progress | null>(null)
  /** Python 每 2 秒推送的記憶體與暫存資料夾用量（狀態列、進度視窗） */
  const [usage, setUsage] = useState<Usage | null>(null)
  /** 進度視窗的狀態：每個階段與「準備檔案」「試跑」（Python 推送） */
  const [runState, setRunState] = useState<RunState | null>(null)
  const runStateRef = useRef<RunState | null>(null)
  /** 進度視窗：處理並匯出、載入資料及預覽、完整跑一次；autoClose 是成功時自動關掉 */
  const [run, setRun] = useState<{
    title: string
    autoClose: boolean
    finished: boolean
    failed: boolean
    result?: string
    error?: { message: string; stage: number | null; step: number | null }
  } | null>(null)
  const [update, setUpdate] = useState<UpdateInfo | null | undefined>(undefined)
  const grid = useRef<GridHandle>(null)
  const stagesPanel = usePanelRef()
  const leftPanel = usePanelRef()
  const rightPanel = usePanelRef()
  const warnPanel = usePanelRef()
  /** 切換下方面板的分頁；收合時順便展開。 */
  const openBottomTab = (tab: 'warnings' | 'types') => {
    setBottomTab(tab)
    if (warnPanel.current?.isCollapsed()) warnPanel.current.expand()
  }

  const gridLoaded = useRef<((total: number) => void) | null>(null)

  /** 換上 Python 回傳的畫面狀態；畫面的文字跟著 Python 記著的語言（在畫面重畫之前換好）。 */
  const applyState = useCallback((next: ViewState) => {
    setLang(next.lang)
    stateRef.current = next
    setState(next)
  }, [])

  /** 最近一次失敗的原因（進度視窗失敗時顯示在紅框裡） */
  const lastError = useRef<string | null>(null)
  /** 改變狀態的呼叫依序一個一個送（Python 收到的順序就是按的順序，例如 ⌘S 一定在名稱送出之後） */
  const queue = useRef<Promise<unknown>>(Promise.resolve())
  /** 步驟表單還沒送出的修改：送其他指令之前先送出（StepForm 登記） */
  const flushForm = useRef<(() => void) | null>(null)
  /** 呼叫 Python：統一處理排隊、忙碌狀態、錯誤與新的畫面狀態。 */
  const call = useCallback(
    <T,>(label: string, fn: () => Promise<Result<T>>): Promise<(T & { ok: true }) | null> => {
      // 表單還沒送出的修改排在這個指令前面：才不會寫到刪除、移動後遞補上來的步驟，也不會蓋掉這次的復原
      flushForm.current?.()
      setPendingCalls((n) => n + 1)
      const run = queue.current.then(async (): Promise<(T & { ok: true }) | null> => {
        lastError.current = null
        try {
          const res = await fn()
          if (!res.ok) {
            lastError.current = res.error
            setStatus({ text: tr(`${label}失敗：${cleanError(res.error)}`, `${label} failed: ${cleanError(res.error)}`), error: true })
            if (res.issues?.length) setReport({ open: true, issues: res.issues, text: res.error })
            return null
          }
          const withState = res as { state?: ViewState }
          if (withState.state) applyState(withState.state)
          return res as T & { ok: true }
        } catch (err) {
          lastError.current = String(err)
          setStatus({ text: tr(`${label}失敗：${cleanError(String(err))}`, `${label} failed: ${cleanError(String(err))}`), error: true })
          return null
        } finally {
          setPendingCalls((n) => n - 1)
        }
      })
      queue.current = run
      return run
    },
    [applyState],
  )

  const runSmoke = useCallback(
    async (target: TabladeApi): Promise<boolean> => {
      const st = await target.selftest()
      if (!st.ok || !st.result.ok) return false
      setSelftest(st.result)
      const loaded = new Promise<number>((resolve) => {
        gridLoaded.current = resolve
        window.setTimeout(() => resolve(-1), SMOKE_TIMEOUT_MS)
      })
      const ex = await target.smoke_example()
      if (!ex.ok) return false
      applyState(ex.state)
      const steps = ex.state.steps
      const pipelineOk = steps.length === 8 && steps.every((s) => s.status === 'ok') && steps[7].rows === 70
      return pipelineOk && (await loaded) === 70
    },
    [applyState],
  )

  useEffect(() => {
    let cancelled = false
    void (async () => {
      const target = await waitForApi()
      const ready = await target.ready()
      if (cancelled) return
      if (!ready.ok) {
        setStatus({ text: tr(`初始化失敗：${ready.error}`, `Initialization failed: ${ready.error}`), error: true })
        return
      }
      setApi(target)
      setInfo(ready)
      onProgress(setProgress)
      onUsage(setUsage)
      onRun((value) => {
        runStateRef.current = value
        setRunState(value)
      })
      // 畫面的語言跟作業系統（不記住選擇）；Python 也用這個語言產生文字
      const detected = detectLang()
      setLang(detected)
      const first = await target.set_language(detected)
      if (first.ok) {
        setInfo({ ...ready, catalog: first.catalog }) // 步驟目錄也是那個語言的
        applyState(first.state)
      }
      setStatus({ text: tr('就緒。先新增或開啟 Recipe。', 'Ready. Create or open a Recipe to start.') })
      // 啟動時查 GitHub 上的最新版號（背景進行，不擋畫面；不自動更新）
      void target.check_update().then((res) => setUpdate(res.ok ? res.update : null))
      if (ready.smoke) await target.smoke_done(await runSmoke(target))
    })()
    return () => {
      cancelled = true
    }
  }, [runSmoke, applyState])

  // ── 動作 ──────────────────────────────────────────────────

  const act = {
    newRecipe: async () => {
      if (!api) return
      const res = await call(tr('新增 Recipe', 'New Recipe'), () => api.new_recipe())
      if (res && !res.cancelled)
        setStatus({
          text: tr(
            `已建立 ${fileName(res.path)}。接著在「讀取資料」加入檔案。`,
            `Created ${fileName(res.path)}. Next, add files in “Read data”.`,
          ),
        })
    },
    openRecipe: async () => {
      if (!api) return
      const res = await call(tr('開啟 Recipe', 'Open Recipe'), () => api.open_recipe())
      if (!res || res.cancelled) return
      const next = res.state.actions.export.ok
        ? tr(
            '可以直接按「處理並匯出資料」，或先按「載入資料及預覽」看每一步的結果。',
            'Click “Process & export” to run it now, or “Load & preview” to see each step first.',
          )
        : `${res.state.actions.export.reason}${tr('。', '.')}`
      const name = fileName(res.state.recipe.path)
      setStatus({ text: tr(`已開啟 ${name}。${next}`, `Opened ${name}. ${next}`) })
      if (res.issues?.length) await showReport()
    },
    save: async (as: boolean) => {
      if (!api) return
      const res = await call(tr('儲存', 'Save'), () => (as ? api.save_recipe_as() : api.save_recipe()))
      if (res && !res.cancelled) setStatus({ text: tr(`已儲存 ${fileName(res.path)}`, `Saved ${fileName(res.path)}`) })
    },
    addFiles: async () => {
      if (!api) return
      const res = await call(tr('加入檔案', 'Add files'), () => api.add_files())
      if (!res || res.cancelled) return
      const n = res.state.input.files.length
      setStatus({
        text: tr(
          `檔案清單共 ${n} 項。按「載入資料及預覽」讀取。`,
          `File list: ${plural(n, 'item', 'items')}. Click “Load & preview” to read them.`,
        ),
      })
    },
    loadData: async () => {
      if (!api) return
      const res = await runStages(tr('載入資料及預覽', 'Load & preview'), true, () =>
        call(tr('載入資料', 'Load data'), () => api.load_data()),
      )
      if (res) {
        const { preview } = res.state
        const n = preview.input_rows
        const sample = preview.mode === 'sample'
        // 有階段算不出來（例如步驟的參數不對）：狀態列也要說，不能只寫「已載入資料」
        const broken = stageProblem(res.state)
        setStatus({
          text:
            tr(
              `已載入資料${n !== null ? `，共 ${n.toLocaleString()} 行` : ''}${sample ? '（大檔：預覽用樣本）' : ''}`,
              `Data loaded${n !== null ? ` (${plural(n, 'line', 'lines')})` : ''}${sample ? '; large files are previewed from a sample' : ''}`,
            ) + (broken ? tr(`。${broken.message}`, `. ${broken.message}`) : ''),
          error: broken !== undefined,
        })
      }
    },
    exportParquet: async () => {
      if (!api) return
      const title = tr('處理並匯出資料', 'Process & export')
      const res = await runStages(title, false, () => call(title, () => api.export_parquet()))
      if (res?.trial_error) {
        const failure = res.trial_error
        setStatus({ text: failure.message, error: true })
        setRun((r) => r && { ...r, failed: true, error: failure })
        return
      }
      if (res && !res.cancelled) {
        const decoded = res.input_warnings
          ? tr(
              `，${res.input_warnings.toLocaleString()} 個檔有解不開的字`,
              `, ${plural(res.input_warnings, 'file', 'files')} with undecodable characters`,
            )
          : ''
        const warn =
          (res.warnings
            ? tr(`，警告 ${res.warnings.toLocaleString()} 則`, `, ${plural(res.warnings, 'warning', 'warnings')}`)
            : '') + decoded
        const how = res.mode === 'stream' ? tr('（串流處理全部資料）', ' (all data streamed)') : ''
        const outs = res.outputs ?? []
        const record = outs.some((o) => o.manifest) ? tr('；處理紀錄寫在 log 資料夾', '; processing log written to the log folder') : ''
        const rowsText = (n: number) => tr(`${n.toLocaleString()} 列`, plural(n, 'row', 'rows'))
        const files =
          outs.length > 1
            ? tr(`${outs.length} 個檔到 output 資料夾：`, `${outs.length} files to the output folder: `) +
              outs.map((o) => `${o.path.split('/').pop()}${tr(`（${rowsText(o.rows)}）`, ` (${rowsText(o.rows)})`)}`).join(tr('、', ', '))
            : `${res.path?.split('/').slice(-2).join('/')}${tr(
                `：${rowsText(res.rows ?? 0)} × ${res.columns} 欄`,
                `: ${rowsText(res.rows ?? 0)} × ${plural(res.columns ?? 0, 'column', 'columns')}`,
              )}`
        // CSV 超過 Excel 的列數上限：Excel 打開會被截斷
        const tooLong = outs.filter((o) => o.format === 'csv' && o.rows > EXCEL_MAX_ROWS).map((o) => o.path.split('/').pop())
        const excel = tooLong.length
          ? tr(
              `。注意：${tooLong.join('、')} 超過 Excel 的 1,048,576 列，用 Excel 打開會被截斷`,
              `. Note: ${tooLong.join(', ')} ${tooLong.length === 1 ? 'has' : 'have'} more than 1,048,576 rows and will be cut off when opened in Excel`,
            )
          : ''
        const text = tr(`已匯出 ${files}${warn}${how}${record}${excel}`, `Exported ${files}${warn}${how}${record}${excel}`)
        setStatus({ text })
        setRun((r) => r && { ...r, result: text })
      }
    },
    downloadSkill: async () => {
      if (!api) return
      const res = await call(tr('下載 Copilot skill', 'Download Copilot skill'), () => api.download_skill())
      if (res && !res.cancelled)
        setStatus({ text: tr(`已存成 ${res.path}（Recipe 版本 ${res.version}）`, `Saved as ${res.path} (Recipe version ${res.version})`) })
    },
    selftest: async () => {
      if (!api) return
      const res = await call(tr('自我檢測', 'Self-test'), () => api.selftest())
      if (res) setSelftest(res.result)
    },
    undo: () => api && call(tr('復原', 'Undo'), () => api.undo()),
    redo: () => api && call(tr('重做', 'Redo'), () => api.redo()),
    /** 切換畫面的語言（Python 記著，不寫設定檔） */
    setLanguage: async (value: Lang) => {
      if (!api || value === lang()) return
      // 畫面的語言等 Python 換好才換（call 照回傳的 state.lang 換）：失敗時兩邊還是同一個語言
      const res = await call(tr('切換語言', 'Switch language'), () => api.set_language(value))
      if (!res) return
      setInfo((i) => i && { ...i, catalog: res.catalog }) // 步驟名稱、表單的欄位標題跟著換
      setStatus({ text: tr('已切換成中文。', 'Switched to English.') })
    },
  }

  /** 會依序跑好幾個階段的動作：開進度視窗（收到第一個進度狀態才顯示；動作被取消時不顯示）。 */
  async function runStages<T>(title: string, autoClose: boolean, fn: () => Promise<T | null>): Promise<T | null> {
    runStateRef.current = null
    setRunState(null)
    setRun({ title, autoClose, finished: false, failed: false })
    const res = await fn()
    // 執行期間 Python 推送的狀態（ref 在 await 之後才會有值）
    const pushed = runStateRef.current as RunState | null
    const rows = [...(pushed?.stages ?? []), ...(pushed?.phases ?? [])]
    const failedRows = rows.filter((s) => s.status === 'error').map((s) => s.name)
    const failed = res === null || failedRows.length > 0
    const cancelled = res !== null && typeof res === 'object' && 'cancelled' in res && res.cancelled === true
    if (cancelled || pushed === null || (autoClose && !failed)) {
      setRun(null)
      setRunState(null)
    } else {
      // 失敗的原因也寫在視窗裡（拿掉「SessionError: 」這類程式的錯誤類別）
      const reason = res === null && lastError.current ? cleanError(lastError.current) : null
      // 呼叫成功、但有階段出錯（例如載入後有步驟的參數不對）：寫是哪個階段的哪一步，可以「前往」
      const next = (res as { state?: ViewState } | null)?.state
      const broken = next && failedRows.length ? stageProblem(next, failedRows) : undefined
      setRun(
        (r) => r && { ...r, finished: true, failed, error: reason ? { message: reason, stage: null, step: null } : (r.error ?? broken) },
      )
    }
    return res
  }

  /** 第一個出錯的階段（只看 names 裡的）：說明哪一步有錯；目前階段的再加上錯誤訊息。step 是 null：前往時選那個階段第一個出錯的步驟 */
  function stageProblem(next: ViewState, names?: string[]) {
    const bad = next.stages.find((s) => s.status === 'error' && (!names || names.includes(s.name)))
    if (!bad) return undefined
    let text = bad.status_text
    if (bad.index === next.current) {
      const step = next.steps.find((s) => s.status === 'error')
      const detail = step ? (step.run_error ?? step.errors[0]?.error) : next.input.error
      if (detail) text += tr(`：${detail}`, `: ${detail}`)
    }
    const message = next.stages.length > 1 ? tr(`階段「${bad.name}」：${text}`, `Stage "${bad.name}": ${text}`) : text
    return { message, stage: bad.index, step: null }
  }

  /** 「檢查並複製錯誤報告」：問題清單和報告文字是 Python 同一次檢查的結果（目前的 Recipe、畫面的語言） */
  const showReport = async () => {
    if (!api) return
    const res = await call(tr('產生錯誤報告', 'Create error report'), () => api.error_report())
    if (res) setReport({ open: true, issues: res.issues, text: res.text })
  }

  /** 切到某個階段，選它的結果（「輸出資料」那一格）。 */
  const goStage = async (index: number) => {
    if (!api) return
    const res = await call(tr('切換階段', 'Switch stage'), () => api.select_stage(index))
    if (res) await call(tr('選取', 'Select'), () => api.select_step(res.state.steps.length))
  }

  /** 切到某個階段的某一步（試跑出錯時「前往這一步」）；step 是 null 時選那個階段第一個出錯的步驟（沒有就選「讀取資料」）。 */
  const goStep = async (stage: number, step: number | null) => {
    if (!api) return
    const res = await call(tr('切換階段', 'Switch stage'), () => api.select_stage(stage))
    if (res) await call(tr('選取', 'Select'), () => api.select_step(step ?? res.state.steps.findIndex((s) => s.status === 'error')))
  }

  /**
   * 表單的修改要送給哪一步：送出時（排到它時）才用步驟的 id 找現在的位置。
   * 中間刪除、移動了步驟也不會寫到遞補上來的另一步；換了階段、或這一步已經刪除時是 -1（不送）。
   */
  const stepFinder = (step: StepState) => {
    const stageName = state?.stages[state.current]?.name
    return () => {
      const now = stateRef.current
      if (!now || now.stages[now.current]?.name !== stageName) return -1
      if (step.id === null) {
        const same = now.steps[step.index]
        return same && same.id === null && same.type === step.type ? step.index : -1
      }
      return now.steps.findIndex((s) => s.id === step.id)
    }
  }

  const addStep = async (type: string, context: AddStepRequest['context'] = {}) => {
    if (!api) return
    const res = await call(tr('新增步驟', 'Add step'), () => api.add_step(type, context))
    if (res) setStatus({ text: tr(`已新增：${res.state.steps[res.index]?.label}`, `Added: ${res.state.steps[res.index]?.label}`) })
  }

  // 快捷鍵（輸入框裡的 ⌘Z 交給瀏覽器處理）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey
      const typing = (e.target as HTMLElement).closest('input, textarea, [contenteditable]')
      if (!mod || !api) return
      const key = e.key.toLowerCase()
      if (key !== 's' && key !== 'o' && key !== 'z') return
      // 處理中、或開著對話框與選單時不動作（跟按鈕一樣）；⌘S 的瀏覽器預設動作（存網頁）照樣擋掉
      const overlay = document.querySelector('[role="dialog"], [role="alertdialog"], [role="menu"]')
      if (busy || overlay) {
        if (key === 's' || key === 'o') e.preventDefault()
        return
      }
      const open = state?.recipe.open ?? false
      if (key === 's' && open) {
        e.preventDefault()
        // 離開輸入框才送出的欄位（Recipe 名稱、輸出檔名）先送出；步驟表單的草稿由 call 先送出。都排在存檔前面
        if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
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

  // 預覽的內容變了才讓表格、型別分頁重新載入：Python 給的指紋（資料、階段、語言、匯出檔變了才換），沒有資料時是 null
  const previewKey = state?.preview.key ?? ''

  const onGridLoaded = useCallback((total: number) => {
    gridLoaded.current?.(total)
    gridLoaded.current = null
  }, [])

  if (!state || !info || !api) {
    return <div className="flex h-svh items-center justify-center text-sm text-muted-foreground">{status.text}</div>
  }

  /** 快捷鍵的寫法：macOS 是 ⌘、⇧⌘，其他是 Ctrl+、Ctrl+Shift+（按鍵兩種都接受） */
  const mac =
    info.platform === 'Darwin' || (info.platform !== 'Windows' && info.platform !== 'Linux' && /Mac|iPhone|iPad/.test(navigator.userAgent))
  const shortcut = (key: string, shift = false) => (mac ? `${shift ? '⇧' : ''}⌘${key}` : `Ctrl+${shift ? 'Shift+' : ''}${key}`)

  const footer = (
    <footer className="flex items-center gap-3 border-t px-3 py-1 text-xs">
      <span role="status" className={`min-w-0 flex-1 truncate ${status.error ? 'text-destructive' : 'text-muted-foreground'}`}>
        {status.text}
      </span>
      {usage ? <UsageStatus usage={usage} /> : null}
      <span className="shrink-0 text-muted-foreground">{`Tablade ${info.version}${tr('・', ' · ')}Recipe ${info.recipe_version}`}</span>
    </footer>
  )
  /** 「⋯」左邊的語言切換鈕：地球圖示＋目前的語言，點開選另一個 */
  const languageMenu = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" aria-label={tr('切換語言', 'Language')} disabled={busy}>
          <Globe /> {LANGS.find((l) => l.value === state.lang)?.label}
          <ChevronDown className="opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-36">
        <DropdownMenuRadioGroup value={state.lang} onValueChange={(v) => void act.setLanguage(v as Lang)}>
          {LANGS.map((l) => (
            <DropdownMenuRadioItem key={l.value} value={l.value}>
              {l.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
  const dialogs = (
    <>
      <ReportDialog
        open={report.open}
        onOpenChange={(open) => setReport((r) => ({ ...r, open }))}
        issues={report.issues}
        report={report.text}
      />
      <SelftestDialog result={info.smoke ? null : selftest} api={api} onClose={() => setSelftest(null)} />
      {run && runState ? (
        <RunDialog
          title={run.title}
          run={runState}
          progress={progress}
          usage={usage}
          busy={busy}
          result={run.result}
          failed={run.failed}
          error={
            run.error
              ? {
                  message: run.error.message,
                  onGo:
                    run.error.stage !== null
                      ? () => {
                          const { stage, step } = run.error!
                          setRun(null)
                          setRunState(null)
                          void goStep(stage!, step)
                        }
                      : undefined,
                }
              : undefined
          }
          onClose={() => {
            setRun(null)
            setRunState(null)
          }}
        />
      ) : null}
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
              {languageMenu}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon-sm" aria-label={tr('更多', 'More')}>
                    <MoreHorizontal />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-60 whitespace-nowrap">
                  <DropdownMenuItem onSelect={() => void act.downloadSkill()}>
                    <Download /> {tr(`下載 Copilot skill（v${info.recipe_version}）`, `Download Copilot skill (v${info.recipe_version})`)}
                  </DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => void act.selftest()}>
                    <Stethoscope /> {tr('自我檢測', 'Self-test')}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </header>
          <main className="flex min-h-0 flex-1 items-center justify-center p-6">
            <div className="w-full max-w-md space-y-4 rounded-lg border p-6 text-center">
              <img src={iconUrl} alt="" className="mx-auto size-12" />
              <h1 className="text-lg font-semibold">{tr('開始整理資料', 'Start cleaning data')}</h1>
              <p className="text-sm text-muted-foreground">
                {tr(
                  '先新增一個 Recipe（會請你選擇存放位置並命名），或開啟已經存好的 Recipe。之後在「讀取資料」加入檔案，再按「載入資料及預覽」。',
                  'Create a Recipe (you will choose where to save it and name it), or open one you saved before. Then add files in “Read data” and click “Load & preview”.',
                )}
              </p>
              <div className="flex justify-center gap-2">
                <Button onClick={() => void act.newRecipe()} disabled={busy}>
                  <FilePlus2 /> {tr('新增 Recipe', 'New Recipe')}
                </Button>
                <Button variant="outline" onClick={() => void act.openRecipe()} disabled={busy}>
                  <FolderOpen /> {tr('開啟 Recipe', 'Open Recipe')}
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
  const readsStages = input.reader === 'stages'
  /** 資料表沒有內容時：沒加檔案、找不到檔、沒選要讀的階段 → 去「讀取資料」；設定變了、還沒載入 → 直接載入 */
  const needsFiles = readsStages ? !input.stages.length || input.error !== null : !input.files.length || !input.can_load
  const emptyPreview = readsStages
    ? !input.stages.length
      ? tr('先在「讀取資料」勾選要讀的前面階段。', 'Select the earlier stages to read in “Read data” first.')
      : input.error
        ? input.error
        : !state.actions.load.ok && state.actions.load.reason
          ? state.actions.load.reason
          : tr(
              '按「載入資料及預覽」依序算出前面的階段、看每一步的結果。只要匯出的話，可以直接按「處理並匯出資料」。',
              'Click “Load & preview” to compute the earlier stages in order and see the result of each step. To just export, click “Process & export”.',
            )
    : !input.files.length
      ? tr('先在「讀取資料」按「瀏覽檔案…」加入檔案。', 'Add files in “Read data” with “Browse files…” first.')
      : !input.can_load
        ? tr(
            '檔案清單有找不到的檔，請先在「讀取資料」移除或修正。',
            'Some files in the list are missing. Remove or fix them in “Read data” first.',
          )
        : input.stale
          ? tr(
              '檔案清單、讀取方式或編碼已變更，按「載入資料及預覽」重新讀取。',
              'The file list, “Read as”, or encoding has changed. Click “Load & preview” to read again.',
            )
          : tr(
              '按「載入資料及預覽」讀取檔案、看每一步的結果。只要匯出的話，可以直接按「處理並匯出資料」。',
              'Click “Load & preview” to read the files and see the result of each step. To just export, click “Process & export”.',
            )

  const outputSelected = state.selected === state.steps.length
  const warnings = state.preview.warnings
  /** 抽樣模式的說明：這個階段讀的檔是樣本時寫出樣本共幾行；樣本來自前面的階段或結合的另一張表時只寫一句 */
function sampleNote(inputRows: number | null | undefined, sample: ViewState['input']['sample']) {
  if (!sample) {
    return tr(
      '預覽用到的資料是樣本，列數、加總、彙總結果可能不正確。',
      'The preview uses sampled data, so row counts, totals and summaries may be inaccurate. ',
    )
  }
  const rows = sample.rows.toLocaleString()
  const head = sample.head.toLocaleString()
  return tr(
    `資料共 ${(inputRows ?? 0).toLocaleString()} 行，預覽只用每個檔的開頭與結尾（共 ${rows} 行，每檔最多前 ${head} 行＋最後 ${sample.tail.toLocaleString()} 行；樣本大小依這台電腦的記憶體決定），列數、加總、彙總結果可能不正確。`,
    `The data has ${plural(inputRows ?? 0, 'line', 'lines')}. The preview uses only the beginning and end of each file (${plural(sample.rows, 'line', 'lines')} in total, at most the first ${head} and last ${plural(sample.tail, 'line', 'lines')} of each file; the sample size depends on this computer's memory), so row counts, totals and summaries may be inaccurate. `,
  )
}

/** 抽樣模式下結果可能不準的步驟：要看整個來源檔的（移除重複、群組彙總、樞紐），和結合的另一張表是樣本的 */
  const sampledNotes = [
    ...state.preview.sampled_steps.map((s) => ({
      key: `whole:${s.stage}:${s.number}`,
      text: tr(
        `${sampledStepText(s)}要看整個來源檔的每一列：用樣本算出來的筆數、加總、彙總結果不是全部資料的。`,
        `${sampledStepText(s)} needs every row of the whole source file: counts, totals and summaries computed from the sample don't cover all data.`,
      ),
    })),
    ...state.preview.sampled_refs.map((s) => ({
      key: `ref:${s.stage}:${s.number}:${s.ref}`,
      text: tr(
        `${sampledStepText(s)}的另一張表 @${s.ref} 是樣本：有些鍵在樣本裡對不到（完整的資料裡可能有），帶進來的欄是空值，「找不到」的警告也可能不正確。`,
        `${sampledStepText(s)}: the other table @${s.ref} is a sample, so some keys don't match in the sample (they may in the full data); the columns brought in are null and “not found” warnings may be wrong.`,
      ),
    })),
  ]
  /** 實際的警告數（warnings 只列出前幾則） */
  const warningCount = state.preview.full ? state.preview.full.warning_count : state.preview.warning_count
  /** 有資料可以畫表格：載入了，或完整模式讀的是匯出檔（匯出不用先載入） */
  const hasData = input.loaded || state.preview.full !== null
  const stage = state.stages[state.current]
  const outputLabel = stage && !stage.last ? tr('階段結果', 'Stage result') : tr('輸出資料', 'Output')
  const stagePrefix = state.stages.length > 1 && stage ? `${stage.name}${tr('・', ' · ')}` : ''
  const blocked = state.preview.blocked
  const previewLabel =
    stagePrefix +
    (state.selected < 0
      ? tr('讀取資料', 'Read data')
      : outputSelected
        ? outputLabel
        : tr(`步驟 ${state.selected + 1}`, `Step ${state.selected + 1}`))
  /** 選的那一步算不出來（步驟有錯）：不顯示資料，改寫原因與「前往」 */
  const currentStage = state.stages[state.current]
  /** 這一步沒有結果（還沒載入或算不出來）：警告分頁不寫「沒有警告」 */
  const noResult = state.preview.rows === null
  /** 來源檔的警告（寬鬆解碼換了字、只認 CRLF 時留在行裡的換行）：不算在步驟的警告數裡，但分頁不能寫「沒有警告」 */
  const decodeWarnings = state.input.file_warnings ?? []
  const decodeNote = decodeWarnings.length
    ? tr(
        `讀取資料有 ${decodeWarnings.length.toLocaleString()} 則警告`,
        `${plural(decodeWarnings.length, 'warning', 'warnings')} while reading`,
      )
    : ''
  const anyWarning = warnings.length > 0 || decodeWarnings.length > 0
  /** 這個階段還沒有資料，是因為讀的（或結合的）前面階段有錯：要先修正那個階段，不是重新載入 */
  const brokenRef =
    !input.loaded && currentStage?.status === 'waiting'
      ? state.stages.find((s) => currentStage.uses.includes(s.name) && s.status === 'error')
      : undefined
  const blockedInfo = (() => {
    if (!blocked || blocked.unloaded) return null
    const which = outputSelected ? outputLabel : tr(`第 ${state.preview.requested + 1} 步`, `Step ${state.preview.requested + 1}`)
    const title = tr(`${which}算不出來`, `${which} can't be computed`)
    // 被停用的步驟 Python 不給是哪一步擋住的：自己找前面第一個出錯的步驟
    const errorStep =
      blocked.step ?? (blocked.stage ? null : state.steps.slice(0, state.preview.requested + 1).findIndex((s) => s.status === 'error'))
    if (errorStep !== null && errorStep >= 0) {
      const n = errorStep + 1
      const step = state.steps[errorStep]
      const error = step?.run_error ?? step?.errors[0]?.error ?? ''
      // 選的就是出錯的那一步才說「這一步」；選「輸出資料」時最後一步有錯，要寫第幾步、可以前往
      const own = errorStep === state.selected
      const reason = error ? tr(`：${error}`, `: ${error}`) : ''
      return {
        badge: own
          ? tr(`${which}算不出來：這一步有錯`, `${which} can't be computed: this step has an error`)
          : tr(`${which}算不出來：第 ${n} 步有錯`, `${which} can't be computed: step ${n} has an error`),
        title,
        detail: own
          ? tr(`這一步有錯${reason}`, `This step has an error${reason}`)
          : tr(`第 ${n} 步「${step?.label ?? ''}」有錯${reason}`, `Step ${n} "${step?.label ?? ''}" has an error${reason}`),
        go: own
          ? null
          : {
              label: tr(`前往第 ${n} 步`, `Go to step ${n}`),
              run: () => void call(tr('選取', 'Select'), () => api!.select_step(errorStep)),
            },
      }
    }
    if (blocked.stage) {
      const target = state.stages.find((s) => s.name === blocked.stage)
      const why = target ? tr(`（${target.status_text}）`, ` (${target.status_text})`) : ''
      return {
        badge: tr(`${which}算不出來：@${blocked.stage} 還沒有結果`, `${which} can't be computed: @${blocked.stage} has no result yet`),
        title,
        detail: tr(`用到的 @${blocked.stage} 還沒有結果${why}`, `@${blocked.stage}, which this uses, has no result yet${why}`),
        go: target ? { label: tr(`前往 @${blocked.stage}`, `Go to @${blocked.stage}`), run: () => void goStage(target.index) } : null,
      }
    }
    return { badge: title, title, detail: tr('前面的步驟算不出來', "An earlier step can't be computed"), go: null }
  })()
  const earlierStages = state.stages
    .slice(0, state.current)
    .map((s) => ({ index: s.index, name: s.name, columns: s.columns, sampled: s.sampled, full: s.full }))
  /** 警告指到的位置：「@階段／檔名［工作表］:行號（原始：…）」 */
  const place = (w: (typeof warnings)[number]) => {
    const slash = '／' // i18n-ignore：來源名稱的寫法（和資料裡的 __source_file 一樣，英文畫面也是）
    const sheet = (name: string | null | undefined) => (name ? tr(`［${name}］`, ` [${name}]`) : '')
    let where = `${w.stage ? `@${w.stage}${slash}` : ''}${w.source_file}${sheet(w.sheet)}:${w.line_no}`
    if (w.orig_source_file != null || w.orig_line_no != null) {
      const orig = `${w.orig_stage ? `@${w.orig_stage}${slash}` : ''}${w.orig_source_file}${sheet(w.orig_sheet)}`
      where += tr(`（原始：${orig}:${w.orig_line_no}）`, ` (original: ${orig}:${w.orig_line_no})`)
    }
    return where
  }

  return (
    <TooltipProvider>
      <div className="flex h-svh flex-col bg-background">
        <header className="flex flex-wrap items-center gap-1 border-b px-3 py-1.5">
          <img src={iconUrl} alt="Tablade" className="size-6" />
          <RecipeTitle
            name={state.recipe.name}
            path={state.recipe.path}
            onCommit={(name) => void call(tr('更新名稱', 'Rename'), () => api.set_meta({ name }))}
          />
          <div className="mx-1 h-5 w-px bg-border" />
          <ToolButton label={tr('新增 Recipe', 'New Recipe')} onClick={() => void act.newRecipe()} disabled={busy}>
            <FilePlus2 />
          </ToolButton>
          <ToolButton
            label={tr(`開啟 Recipe（${shortcut('O')}）`, `Open Recipe (${shortcut('O')})`)}
            onClick={() => void act.openRecipe()}
            disabled={busy}
          >
            <FolderOpen />
          </ToolButton>
          <ToolButton
            label={tr(
              `儲存 Recipe（${shortcut('S')}，${shortcut('S', true)} 另存）`,
              `Save Recipe (${shortcut('S')}; ${shortcut('S', true)} to save as)`,
            )}
            onClick={() => void act.save(false)}
            disabled={busy}
          >
            <Save />
          </ToolButton>
          <div className="mx-1 h-5 w-px bg-border" />
          <ToolButton
            label={tr(`復原（${shortcut('Z')}）`, `Undo (${shortcut('Z')})`)}
            onClick={() => void act.undo()}
            disabled={busy || !state.can_undo}
          >
            <Undo2 />
          </ToolButton>
          <ToolButton
            label={tr(`重做（${shortcut('Z', true)}）`, `Redo (${shortcut('Z', true)})`)}
            onClick={() => void act.redo()}
            disabled={busy || !state.can_redo}
          >
            <Redo2 />
          </ToolButton>
          <div className="mx-1 h-5 w-px bg-border" />
          <Tooltip>
            <TooltipTrigger asChild>
              <span>
                <Button size="sm" variant="outline" onClick={() => void act.loadData()} disabled={busy || !state.actions.load.ok}>
                  <FileInput /> {tr('載入資料及預覽', 'Load & preview')}
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-80">
              {state.actions.load.reason ??
                tr(
                  '依序讀取全部階段的檔案（大檔讀樣本），算出每一步的預覽；只重讀有變更的階段',
                  'Read the files of every stage in order (a sample for large files) and preview each step; only changed stages are read again',
                )}
            </TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="ml-1">
                <Button size="sm" onClick={() => void act.exportParquet()} disabled={busy || !state.actions.export.ok}>
                  <Play /> {tr('處理並匯出資料', 'Process & export')}
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-80">
              {state.actions.export.reason ??
                tr(
                  '處理全部資料，存到 output 資料夾。不用先載入；沒預覽過時會先試跑。',
                  'Process all data and save it to the output folder. No need to load first; a trial run comes first if you have not previewed.',
                )}
            </TooltipContent>
          </Tooltip>
          {state.recipe.dirty ? <span className="ml-1.5 text-xs text-muted-foreground">{tr('還沒儲存', 'Unsaved')}</span> : null}
          <div className="ml-auto flex items-center gap-1">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm">
                  <Bot /> Copilot
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-60 whitespace-nowrap">
                <DropdownMenuItem onSelect={() => void showReport()}>
                  <TriangleAlert /> {tr('檢查並複製錯誤報告', 'Check and copy error report')}
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => void act.downloadSkill()}>
                  <Download /> {tr(`下載 Copilot skill（v${info.recipe_version}）`, `Download Copilot skill (v${info.recipe_version})`)}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            {languageMenu}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon-sm" aria-label={tr('更多', 'More')}>
                  <MoreHorizontal />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-48 whitespace-nowrap">
                <DropdownMenuItem onSelect={() => void act.save(true)}>
                  <Save /> {tr('另存 Recipe…', 'Save Recipe as…')}
                </DropdownMenuItem>
                <DropdownMenuCheckboxItem checked={showSystem} onCheckedChange={(v) => setShowSystem(v === true)}>
                  <Columns3 /> {tr('顯示系統欄位', 'Show system columns')}
                </DropdownMenuCheckboxItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => void act.selftest()}>
                  <Stethoscope /> {tr('自我檢測', 'Self-test')}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>

        <ResizablePanelGroup orientation="horizontal" className="min-h-0 flex-1">
          <ResizablePanel defaultSize="12" minSize={120} collapsible collapsedSize={RAIL_WIDTH} panelRef={stagesPanel}>
            <CollapsiblePane side="left" label={tr('階段', 'Stages')} onExpand={() => stagesPanel.current?.expand()}>
              <StagesBar
                state={state}
                busy={busy}
                onCollapse={() => stagesPanel.current?.collapse()}
                onSelect={(i) => void call(tr('切換階段', 'Switch stage'), () => api.select_stage(i))}
                onAdd={async (name, before) => {
                  const res = await call(tr('新增階段', 'Add stage'), () => api.add_stage(name, before))
                  if (res)
                    setStatus({
                      text: tr(
                        `已新增階段「${name}」。接著在「讀取資料」選擇要讀的檔案或前面的階段。`,
                        `Added stage "${name}". Next, choose the files or earlier stages to read in “Read data”.`,
                      ),
                    })
                  return res !== null
                }}
                onRename={async (i, name) => (await call(tr('階段改名', 'Rename stage'), () => api.rename_stage(i, name))) !== null}
                onMove={(i, to) => void call(tr('移動階段', 'Move stage'), () => api.move_stage(i, to))}
                onDelete={(i) => void call(tr('刪除階段', 'Delete stage'), () => api.delete_stage(i))}
              />
            </CollapsiblePane>
          </ResizablePanel>
          <ResizableHandle />
          <ResizablePanel defaultSize="20" minSize={220} collapsible collapsedSize={RAIL_WIDTH} panelRef={leftPanel}>
            <CollapsiblePane side="left" label={tr('步驟', 'Steps')} onExpand={() => leftPanel.current?.expand()}>
              <StepsPanel
                state={state}
                catalog={info.catalog}
                busy={busy}
                onSelect={(i) => void call(tr('選取', 'Select'), () => api.select_step(i))}
                onAdd={(type) => void addStep(type)}
                onMove={(i, to) => void call(tr('移動步驟', 'Move step'), () => api.move_step(i, to))}
                onToggle={(i) => void call(tr('切換步驟', 'Toggle step'), () => api.toggle_step(i))}
                onDelete={(i) => void call(tr('刪除步驟', 'Delete step'), () => api.delete_step(i))}
                onCollapse={() => leftPanel.current?.collapse()}
              />
            </CollapsiblePane>
          </ResizablePanel>
          <ResizableHandle />
          <ResizablePanel defaultSize="45" minSize="30">
            <ResizablePanelGroup orientation="vertical">
              <ResizablePanel defaultSize="78" minSize="30">
                <div className="flex h-full flex-col">
                  <PaneHeader>
                    {/* 標題就是顯示的是什麼（跟其他面板的標題同一個樣式）；太長時截斷，滑鼠移上去看全名 */}
                    <span className="min-w-0 truncate px-1 text-sm font-medium" title={previewLabel}>
                      {previewLabel}
                    </span>
                    {state.preview.rows !== null ? (
                      <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                        {tr(
                          `${state.preview.rows.toLocaleString()} 列 × ${state.preview.columns.filter((c) => !c.system).length} 欄`,
                          `${plural(state.preview.rows, 'row', 'rows')} × ${plural(state.preview.columns.filter((c) => !c.system).length, 'column', 'columns')}`,
                        )}
                      </span>
                    ) : null}
                    {state.preview.mode === 'sample' ? (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Badge variant="outline" className="border-amber-500/50 bg-amber-500/10 text-amber-700 dark:text-amber-400">
                            {tr('抽樣模式', 'Sample mode')}
                          </Badge>
                        </TooltipTrigger>
                        <TooltipContent className="max-w-80">
                          {sampleNote(state.preview.input_rows, state.input.sample)}
                          {/* CSV 輸出匯出後不提供完整模式：不說「輸出資料會改成讀匯出檔」 */}
                          {currentStage?.output?.format === 'csv'
                            ? tr(
                                '按「處理並匯出資料」處理全部資料（輸出 CSV：「輸出資料」照舊是抽樣模式，完整結果看匯出的檔）。',
                                'Click “Process & export” to process all data (CSV output: “Output” stays in sample mode; see the exported file for the full result).',
                              )
                            : tr(
                                '按「處理並匯出資料」看完整結果（匯出後「輸出資料」會改成讀匯出檔）。',
                                'Click “Process & export” for the full result (afterwards “Output” reads the exported file).',
                              )}
                        </TooltipContent>
                      </Tooltip>
                    ) : state.preview.mode === 'full' ? (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Badge variant="outline" className="border-green-600/40 bg-green-600/10 text-green-700 dark:text-green-400">
                            {tr('完整模式', 'Full mode')}
                          </Badge>
                        </TooltipTrigger>
                        <TooltipContent className="max-w-80">
                          {state.preview.full
                            ? tr(
                                `已處理全部 ${state.preview.full.input_rows.toLocaleString()} 行（${state.preview.full.time} 匯出），顯示的是匯出的檔。`,
                                `${state.preview.full.input_rows === 1 ? 'The only line was' : `All ${plural(state.preview.full.input_rows, 'line', 'lines')} were`} processed (exported ${state.preview.full.time}); showing the exported file.`,
                              )
                            : tr(
                                `資料共 ${state.preview.input_rows?.toLocaleString()} 行，全部讀進來（沒有抽樣），列數、加總都是全部資料的。`,
                                `The data has ${plural(state.preview.input_rows ?? 0, 'line', 'lines')}, all read in (no sampling), so row counts and totals cover all data.`,
                              )}
                        </TooltipContent>
                      </Tooltip>
                    ) : null}
                    {/* 抽樣模式下有要看整個來源檔的步驟、或結合的另一張表是樣本：紅色警告，滑鼠移上去列出是哪幾步 */}
                    {sampledNotes.length > 0 ? (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Badge variant="destructive">{tr('警告：預覽結果可能不正確', 'Warning: preview may be inaccurate')}</Badge>
                        </TooltipTrigger>
                        <TooltipContent className="max-w-80 flex-col items-start gap-1">
                          {sampledNotes.length === 1 ? (
                            <div>{sampledNotes[0].text}</div>
                          ) : (
                            <>
                              <div>
                                {tr(
                                  '下面這些步驟用到樣本，結果可能不正確：',
                                  'These steps use a sample, so their results may be inaccurate:',
                                )}
                              </div>
                              <ul className="list-disc pl-4">
                                {sampledNotes.map((n) => (
                                  <li key={n.key}>{n.text}</li>
                                ))}
                              </ul>
                            </>
                          )}
                          <div className="opacity-80">
                            {state.preview.sampled_refs.length
                              ? tr(
                                  '按「處理並匯出資料」看完整結果；結合的另一張表也可以在那一步的設定按「完整跑一次」。',
                                  'Click “Process & export” for the full result, or click “Run in full” in the join step’s settings to compute the other table from all data.',
                                )
                              : tr('按「處理並匯出資料」看完整結果。', 'Click “Process & export” for the full result.')}
                          </div>
                        </TooltipContent>
                      </Tooltip>
                    ) : null}
                    {brokenRef ? (
                      <Badge variant="destructive">{currentStage?.status_text}</Badge>
                    ) : !hasData ? (
                      <Badge variant="outline" className="text-muted-foreground">
                        {currentStage?.status_text ?? tr('還沒載入資料', 'Data not loaded yet')}
                      </Badge>
                    ) : blockedInfo ? (
                      <Badge variant="destructive">{blockedInfo.badge}</Badge>
                    ) : null}
                  </PaneHeader>
                  <div className="min-h-0 flex-1">
                    {brokenRef ? (
                      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center text-sm">
                        <p className="font-medium text-destructive">{tr('還算不出來', "Can't be computed yet")}</p>
                        <p className="max-w-md text-muted-foreground">
                          {tr(
                            `讀的「${brokenRef.name}」${brokenRef.status_text}，先修正那個階段。`,
                            `"${brokenRef.name}", which this reads: ${brokenRef.status_text}. Fix that stage first.`,
                          )}
                        </p>
                        <Button size="sm" variant="outline" onClick={() => void goStage(brokenRef.index)}>
                          {tr(`前往 @${brokenRef.name}`, `Go to @${brokenRef.name}`)}
                        </Button>
                      </div>
                    ) : blockedInfo ? (
                      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center text-sm">
                        <p className="font-medium text-destructive">{blockedInfo.title}</p>
                        <p className="max-w-md break-all text-muted-foreground">{blockedInfo.detail}</p>
                        {blockedInfo.go ? (
                          <Button size="sm" variant="outline" onClick={blockedInfo.go.run}>
                            {blockedInfo.go.label}
                          </Button>
                        ) : null}
                      </div>
                    ) : hasData ? (
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
                        {!needsFiles ? (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={busy || !state.actions.load.ok}
                            title={state.actions.load.reason ?? undefined}
                            onClick={() => void act.loadData()}
                          >
                            <FileInput /> {tr('載入資料及預覽', 'Load & preview')}
                          </Button>
                        ) : state.selected !== -1 ? (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => {
                              rightPanel.current?.expand()
                              void call(tr('選取', 'Select'), () => api.select_step(-1))
                            }}
                          >
                            {tr('前往「讀取資料」', 'Go to “Read data”')}
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
                  <PaneHeader className={anyWarning ? 'bg-amber-500/10' : ''}>
                    <div role="tablist" aria-label={tr('下方面板', 'Bottom panel')} className="flex items-center gap-1">
                      <BottomTab active={bottomTab === 'warnings'} onClick={() => openBottomTab('warnings')}>
                        {anyWarning ? (
                          <TriangleAlert className="size-4 shrink-0 text-amber-600 dark:text-amber-400" />
                        ) : noResult ? (
                          <TriangleAlert className="size-4 shrink-0 text-muted-foreground" />
                        ) : (
                          <CircleCheck className="size-4 shrink-0 text-green-600" />
                        )}
                        <span className={anyWarning ? 'text-amber-700 dark:text-amber-400' : noResult ? 'text-muted-foreground' : ''}>
                          {state.preview.full
                            ? state.preview.full.warning_count
                              ? tr(
                                  `匯出時共 ${state.preview.full.warning_count.toLocaleString()} 則警告`,
                                  `${plural(state.preview.full.warning_count, 'warning', 'warnings')} at export`,
                                )
                              : decodeNote || tr('匯出時沒有警告', 'No warnings at export')
                            : warningCount
                              ? tr(`警告（${warningCount.toLocaleString()}）`, `Warnings (${warningCount.toLocaleString()})`)
                              : decodeNote || (noResult ? tr('警告', 'Warnings') : tr('沒有警告', 'No warnings'))}
                          {decodeNote && (warningCount || state.preview.full?.warning_count) ? tr(`；${decodeNote}`, `; ${decodeNote}`) : ''}
                        </span>
                      </BottomTab>
                      <BottomTab active={bottomTab === 'types'} onClick={() => openBottomTab('types')}>
                        <Shapes className="size-4 shrink-0" />
                        {tr('型別', 'Types')}
                      </BottomTab>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      className="ml-auto"
                      aria-label={tr('收合或展開下方面板', 'Collapse or expand the bottom panel')}
                      title={tr('收合或展開下方面板', 'Collapse or expand the bottom panel')}
                      onClick={() => (warnPanel.current?.isCollapsed() ? warnPanel.current.expand() : warnPanel.current?.collapse())}
                    >
                      <ChevronDown className="warn-collapse" />
                      <ChevronUp className="warn-expand" />
                    </Button>
                  </PaneHeader>
                  {bottomTab === 'types' ? (
                    <div className="min-h-0 flex-1 overflow-auto">
                      <TypesPane api={api} step={state.preview.step} resetKey={previewKey} showSystem={showSystem} />
                    </div>
                  ) : (
                    <ul className="flex-1 overflow-auto text-sm">
                      {decodeWarnings.map((w, i) => (
                        <li
                          key={`decode:${w.file}:${i}` /* i18n-ignore：React 的 key，不是畫面文字 */}
                          className="flex items-center gap-2 px-3 py-0.5 text-xs"
                        >
                          <TriangleAlert className="size-3.5 shrink-0 text-amber-600" />
                          <span className="max-w-[50%] min-w-0 shrink-0 truncate font-mono text-muted-foreground" title={w.file}>
                            {w.file}
                          </span>
                          <span className="min-w-0 flex-1 truncate" title={w.text}>
                            {w.text}
                          </span>
                        </li>
                      ))}
                      {warnings.map((w, i) => (
                        <li key={i}>
                          <button
                            type="button"
                            className="flex w-full items-center gap-2 px-3 py-0.5 text-left text-xs hover:bg-muted"
                            onClick={async () => {
                              if (w.source_file === null || w.line_no === null) return
                              const res = await api.find_row(state.preview.step, w.source_file, w.line_no, w.sheet, w.stage)
                              if (res.ok && res.row !== null) grid.current?.scrollToRow(res.row)
                            }}
                          >
                            <TriangleAlert className="size-3.5 shrink-0 text-amber-600" />
                            {/* 位置最多佔一半（太長就截斷，滑鼠移上去看全文），訊息至少有一半的寬度 */}
                            <span className="max-w-[50%] min-w-0 shrink-0 truncate font-mono text-muted-foreground" title={place(w)}>
                              {place(w)}
                            </span>
                            <span className="min-w-0 flex-1 truncate" title={w.message}>
                              {w.message}
                            </span>
                          </button>
                        </li>
                      ))}
                      {warningCount > warnings.length ? (
                        <li className="px-3 py-1 text-xs text-muted-foreground">
                          {tr(
                            `只列出前 ${warnings.length.toLocaleString()} 則（共 ${warningCount.toLocaleString()} 則）。`,
                            `Showing the first ${warnings.length.toLocaleString()} of ${plural(warningCount, 'warning', 'warnings')}.`,
                          )}
                        </li>
                      ) : null}
                    </ul>
                  )}
                </div>
              </ResizablePanel>
            </ResizablePanelGroup>
          </ResizablePanel>
          <ResizableHandle />
          <ResizablePanel defaultSize="23" minSize={280} collapsible collapsedSize={RAIL_WIDTH} panelRef={rightPanel}>
            <CollapsiblePane side="right" label={tr('設定', 'Settings')} onExpand={() => rightPanel.current?.expand()}>
              <div className="flex h-full flex-col">
                <PaneHeader>
                  <CollapseButton side="right" label={tr('設定', 'Settings')} onClick={() => rightPanel.current?.collapse()} />
                  <PaneTitle>{tr('設定', 'Settings')}</PaneTitle>
                </PaneHeader>
                <div className="min-h-0 flex-1 overflow-auto p-4">
                  {selected && catalogItem ? (
                    <StepForm
                      key={`${stage?.name ?? ''}/${selected.id ?? `#${selected.index}`}`}
                      step={selected}
                      item={catalogItem}
                      flushRef={flushForm}
                      onChange={(values) => {
                        const at = stepFinder(selected)
                        return call(tr('更新步驟', 'Update step'), () => {
                          const index = at()
                          // 這一步已經刪除（或已經換到別的階段）：這次修改沒有地方放，不送
                          return index < 0 ? Promise.resolve<Result<object>>({ ok: true }) : api.update_step(index, values)
                        })
                      }}
                      onRefresh={(field, values) => {
                        const at = stepFinder(selected)
                        return call(tr('重新從資料讀取', 'Reload from data'), () => {
                          const index = at()
                          return index < 0 ? Promise.resolve<Result<object>>({ ok: true }) : api.refresh_values(index, field, values)
                        })
                      }}
                      context={{
                        stages: earlierStages,
                        onGoStage: (i) => void goStage(i),
                        onFullRun: (i) =>
                          void runStages(tr('完整跑一次', 'Run in full'), true, () =>
                            call(tr('完整跑一次', 'Run in full'), () => api.full_run(i)),
                          ),
                      }}
                    />
                  ) : selected ? (
                    <UnknownStep
                      step={selected}
                      busy={busy}
                      onDelete={() => void call(tr('刪除步驟', 'Delete step'), () => api.delete_step(selected.index))}
                    />
                  ) : outputSelected ? (
                    <OutputPanel
                      state={state}
                      onOutput={(v) => void call(tr('更新輸出設定', 'Update output settings'), () => api.set_output(v))}
                      onStageOutput={(index, file, format) =>
                        void call(tr('更新輸出檔名', 'Update output file name'), () => api.set_stage_output(index, file, format))
                      }
                    />
                  ) : (
                    <InputPanel
                      state={state}
                      busy={busy}
                      onAddFiles={() => void act.addFiles()}
                      onAddEntry={(text) => void call(tr('加入路徑', 'Add path'), () => api.add_file_entry(text))}
                      onRemove={(i) => void call(tr('移除檔案', 'Remove file'), () => api.remove_file(i))}
                      onInput={(v) => void call(tr('更新資料設定', 'Update read settings'), () => api.set_input(v))}
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
