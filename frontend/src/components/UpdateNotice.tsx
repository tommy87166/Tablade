/**
 * 起始頁的版本資訊：啟動時查到 GitHub 上有新版就提示。
 * 能自動更新時（發布的 App）顯示「更新並重新啟動」：按了才下載、驗章、換檔（selfupdate.py），
 * 成功後 Python 關掉視窗並啟動新版。只在起始頁出現，那時沒有開著的 Recipe，不會有未存的修改。
 * 不能自動更新時（例如從原始碼執行）寫原因，並給下載頁的網址。
 */
import { Check, Copy, Download, LoaderCircle } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { tr } from '@/i18n'
import { onUpdating, type TabladeApi, type UpdateInfo, type UpdateProgress } from '../bridge'

/**
 * 沒查到最新版的原因。Python 給了代碼時用畫面目前的語言寫（切換語言會跟著換）；
 * 沒有代碼時只好用 Python 啟動當下的語言寫好的 error。
 */
function reason(update: UpdateInfo): string | null {
  switch (update.error_code) {
    case 'disabled':
      return tr('已關閉檢查更新', 'Update check is turned off')
    case 'no_release':
      return tr('GitHub 上還沒有發布版本', 'No releases on GitHub yet')
    case 'http':
      return tr(`無法檢查更新（HTTP ${update.http_status ?? '?'}）`, `Couldn't check for updates (HTTP ${update.http_status ?? '?'})`)
    case 'offline':
      return tr('無法連線到 GitHub，略過檢查更新', "Couldn't connect to GitHub; skipped the update check")
    case 'tls':
      return tr('無法驗證 GitHub 的憑證，略過檢查更新', "Couldn't verify GitHub's certificate; skipped the update check")
    case 'bad_version':
      return tr('無法判讀最新版號', "Couldn't read the latest version number")
    default:
      return update.error
  }
}

const MB = 1024 * 1024

/** 進度的文字：下載到多少、驗章、解壓、換檔、重新啟動 */
function stepText(p: UpdateProgress): string {
  switch (p.phase) {
    case 'download': {
      const done = (p.done / MB).toFixed(0)
      return p.total
        ? tr(`正在下載 v${p.version}…（${done} / ${(p.total / MB).toFixed(0)} MB）`, `Downloading v${p.version}… (${done} / ${(p.total / MB).toFixed(0)} MB)`)
        : tr(`正在下載 v${p.version}…（${done} MB）`, `Downloading v${p.version}… (${done} MB)`)
    }
    case 'verify':
      return tr('正在驗證簽章…', 'Verifying the signature…')
    case 'extract':
      return tr('正在解壓…', 'Unpacking…')
    case 'install':
      return tr('正在換成新版…', 'Replacing the app…')
    case 'restart':
      return tr('即將重新啟動…', 'Restarting…')
  }
}

interface Props {
  api: TabladeApi
  current: string
  /** undefined：還在查；null：不檢查（smoke test） */
  update: UpdateInfo | null | undefined
}

export function UpdateNotice({ api, current, update }: Props) {
  const field = useRef<HTMLInputElement>(null)
  const [copied, setCopied] = useState(false)
  const [running, setRunning] = useState(false)
  const [step, setStep] = useState<UpdateProgress | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => onUpdating(setStep), [])
  if (update === null) return null
  if (update === undefined)
    return (
      <p className="text-xs text-muted-foreground">
        {tr(`目前版本 v${current}・正在檢查更新…`, `Current version v${current} · Checking for updates…`)}
      </p>
    )
  if (!update.newer) {
    return (
      <p className="text-xs text-muted-foreground">
        {tr(`目前版本 v${current}・${reason(update) ?? '已是最新版'}`, `Current version v${current} · ${reason(update) ?? 'Up to date'}`)}
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
  const install = async () => {
    if (!update.latest) return
    setRunning(true)
    setError(null)
    const res = await api.install_update(update.latest)
    if (!res.ok) {
      setRunning(false)
      setError(res.error.replace(/^UpdateError: /, ''))
    }
    // 成功時 Python 會關掉視窗並啟動新版，按鈕維持「進行中」
  }
  return (
    <div className="space-y-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-left text-sm">
      <p className="font-medium text-amber-800 dark:text-amber-300">
        {tr(
          `有新版本 v${update.latest}（目前 v${update.current}）`,
          `New version v${update.latest} available (current v${update.current})`,
        )}
      </p>
      {update.install?.ok ? (
        <>
          <p className="text-xs text-muted-foreground">
            {tr(
              '按下去才會下載（約 70 MB），驗證簽章後換成新版並重新啟動；舊版會留到新版開啟後才刪掉。',
              'Nothing is downloaded until you click (about 70 MB). The signature is verified, then Tablade is replaced and restarts; the old version is removed after the new one opens.',
            )}
          </p>
          <div className="flex items-center gap-2">
            <Button size="sm" disabled={running} onClick={() => void install()}>
              {running ? <LoaderCircle className="animate-spin" /> : <Download />}
              {tr('更新並重新啟動', 'Update and restart')}
            </Button>
            {running && step ? <span className="text-xs text-muted-foreground">{stepText(step)}</span> : null}
          </div>
          {error ? (
            <p role="alert" className="text-xs break-words text-destructive">
              {tr(`更新失敗，目前的版本沒有變動：${error}`, `Update failed; this version is unchanged: ${error}`)}
            </p>
          ) : null}
        </>
      ) : (
        <p className="text-xs text-muted-foreground">
          {update.install?.reason
            ? tr(`${update.install.reason}。請到下面的網址下載新版。`, `${update.install.reason}. Download the new version from the URL below.`)
            : tr('請到下面的網址下載新版。', 'Download the new version from the URL below.')}
        </p>
      )}
      <div className="flex gap-1.5">
        <input
          ref={field}
          readOnly
          value={update.url}
          aria-label={tr('新版下載網址', 'Download URL for the new version')}
          onFocus={(e) => e.currentTarget.select()}
          className="h-8 min-w-0 flex-1 rounded-md border bg-background px-2 font-mono text-xs"
        />
        <Button size="sm" variant="outline" onClick={() => void copy()}>
          {copied ? <Check /> : <Copy />} {copied ? tr('已複製', 'Copied') : tr('複製網址', 'Copy URL')}
        </Button>
      </div>
    </div>
  )
}
