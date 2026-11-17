"""步骤 1：构建主表与参照表（公开数据集 + 确定性身份字段）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".pylibs"))

from src.pipeline import make_dataset

if __name__ == "__main__":
    make_dataset.main()
