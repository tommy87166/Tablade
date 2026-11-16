"use strict";

// 資料內容一律用 textContent 寫入，不用 innerHTML：
// 資料可能含有 HTML，而這個頁面可以呼叫 Python，不能讓資料變成可執行的程式。

const $ = (id) => document.getElementById(id);
const buttons = {
  open: $("btn-open"),
  export: $("btn-export"),
  selftest: $("btn-selftest"),
};

let api = null;
let hasData = false;

function setStatus(text, isError = false) {
  const el = $("status");
  el.textContent = text;
  el.classList.toggle("error", isError);
}

function setBusy(busy) {
  buttons.open.disabled = busy;
  buttons.selftest.disabled = busy;
  buttons.export.disabled = busy || !hasData;
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

async function call(label, fn) {
  setBusy(true);
  setStatus(`${label}…`);
  try {
    const res = await fn();
    if (!res.ok) {
      setStatus(`${label}失敗：${res.error}`, true);
      return null;
    }
    return res;
  } catch (err) {
    setStatus(`${label}失敗：${err}`, true);
    return null;
  } finally {
    setBusy(false);
  }
}

function renderSelftest(result) {
  const list = $("selftest-checks");
  list.replaceChildren();
  for (const c of result.checks) {
    const li = document.createElement("li");
    li.className = c.ok ? "ok" : "ng";
    const mark = document.createElement("span");
    mark.className = "mark";
    mark.textContent = c.ok ? "OK" : "NG";
    li.append(mark, `${c.name}：${c.detail}`);
    list.append(li);
  }
  $("selftest-versions").textContent = Object.entries(result.versions)
    .map(([k, v]) => `${k} ${v}`)
    .join("　");
  $("selftest").hidden = false;
}

function renderPreview(res) {
  $("preview-title").textContent = `預覽：${res.file}`;
  $("preview-meta").textContent =
    `${res.rows.toLocaleString()} 列 × ${res.columns.length} 欄　` +
    `編碼 ${res.encoding}　${formatBytes(res.bytes)}　（顯示前 ${res.preview.length} 列）`;

  const table = $("preview-table");
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  const corner = document.createElement("th");
  corner.className = "rownum";
  corner.textContent = "#";
  headRow.append(corner);
  for (const name of res.columns) {
    const th = document.createElement("th");
    th.textContent = name;
    headRow.append(th);
  }
  thead.append(headRow);

  const tbody = document.createElement("tbody");
  res.preview.forEach((row, i) => {
    const tr = document.createElement("tr");
    const num = document.createElement("td");
    num.className = "rownum";
    num.textContent = String(i + 1);
    tr.append(num);
    for (const value of row) {
      const td = document.createElement("td");
      if (value === null) {
        td.className = "null";
        td.textContent = "null";
      } else {
        td.textContent = String(value);
      }
      tr.append(td);
    }
    tbody.append(tr);
  });

  table.replaceChildren(thead, tbody);
  $("preview").hidden = false;
}

async function runSelftest() {
  const res = await call("自我檢測", () => api.selftest());
  if (!res) return false;
  renderSelftest(res.result);
  setStatus(res.result.ok ? "自我檢測通過" : "自我檢測失敗", !res.result.ok);
  return res.result.ok;
}

async function openCsv() {
  const res = await call("讀取 CSV", () => api.open_csv());
  if (!res || res.cancelled) {
    if (res) setStatus("已取消");
    return;
  }
  hasData = true;
  setBusy(false);
  renderPreview(res);
  setStatus(`已讀取 ${res.file}`);
}

async function exportParquet() {
  const res = await call("匯出 Parquet", () => api.export_parquet());
  if (!res || res.cancelled) {
    if (res) setStatus("已取消");
    return;
  }
  setStatus(`已匯出 ${res.file}：${res.rows.toLocaleString()} 列，${formatBytes(res.bytes)}`);
}

window.addEventListener("pywebviewready", async () => {
  api = window.pywebview.api;
  const info = await api.ready();
  if (!info.ok) {
    setStatus(`初始化失敗：${info.error}`, true);
    return;
  }
  $("footer").textContent =
    `Tablade ${info.version}　Polars ${info.polars}　${info.platform}　完全離線執行`;
  setBusy(false);
  setStatus("就緒。可以先按「自我檢測」確認環境。");

  buttons.open.addEventListener("click", openCsv);
  buttons.export.addEventListener("click", exportParquet);
  buttons.selftest.addEventListener("click", runSelftest);

  if (info.smoke) {
    const passed = await runSelftest();
    await api.smoke_done(passed);
  }
});
