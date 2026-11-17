"""步骤 2：按固定随机种子注入八类缺陷，并写出缺陷真值。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".pylibs"))

from src.pipeline import inject

if __name__ == "__main__":
    inject.main()
