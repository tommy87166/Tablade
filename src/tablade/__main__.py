"""``python -m tablade`` 與 PyInstaller 的進入點。"""

import sys

from tablade.cli import main

if __name__ == "__main__":
    sys.exit(main())
