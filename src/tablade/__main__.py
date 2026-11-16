"""``python -m tablade`` 與 PyInstaller 的進入點。"""

import multiprocessing
import sys

from tablade.cli import main

if __name__ == "__main__":
    # 打包後的 App 開平行轉碼的工作程序時，工作程序也從這裡啟動：交給 multiprocessing 處理
    multiprocessing.freeze_support()
    sys.exit(main())
