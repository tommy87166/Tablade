import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'
import { viteSingleFile } from 'vite-plugin-singlefile'

/**
 * 正式版的 Content-Security-Policy（只在 build 時注入）。
 * - default-src 'none'：頁面不能連線、嵌入或載入任何外部資源
 * - script-src 'unsafe-inline'：整個 app 內嵌在一份 HTML 裡
 * - 'unsafe-eval'：pywebview 的 JS bridge 需要，拿掉會讓 window.pywebview.api 無法使用
 * - img-src data:：圖示以 data URI 內嵌
 */
export const CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline' 'unsafe-eval'",
  "style-src 'unsafe-inline'",
  'img-src data:',
].join('; ')

function injectCsp(): Plugin {
  return {
    name: 'tablade-csp',
    apply: 'build',
    transformIndexHtml: {
      order: 'pre',
      // 直接插入原字串（用 tag 描述會把引號轉成 &#39;）
      handler: (html) =>
        html.replace('<head>', `<head>\n    <meta http-equiv="Content-Security-Policy" content="${CSP}" />`),
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  // 產出單一 HTML：Python 以字串載入（WebView2 上限 2 MB），不開 HTTP server
  plugins: [react(), tailwindcss(), viteSingleFile(), injectCsp()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, './src') },
  },
  server: {
    // 允許讀取 repo 根目錄的 assets/icon.svg
    fs: { allow: [path.resolve(import.meta.dirname, '..')] },
  },
  build: {
    // 所有資源一律內嵌
    assetsInlineLimit: Number.MAX_SAFE_INTEGER,
  },
})
