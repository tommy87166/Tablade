/** 步驟名稱下方的橘色小字：每個來源檔分別處理，或與來源檔分檔無關（文字由 Python 提供）。 */
export function StepScope({ scope }: { scope: string }) {
  return <div className="text-[11px] font-normal text-primary!">{scope}</div>
}
