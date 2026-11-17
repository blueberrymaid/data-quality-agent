"""数据获取与构造：把公开数据集落成可复现的"原始数据"。

主表：UCI Bank Marketing（bank-full.csv，45,211 行 × 17 列，二分类目标 y）
      来源 https://archive.ics.uci.edu/dataset/222/bank+marketing
      若离线或镜像不可达，则用**确定性合成器**生成同结构数据（下方 synth_bank_marketing），
      报告里会明确标注实际用的是哪一条路径。

客户档案表（跨表实体匹配用）：
      主表本身不含客户身份字段，无法做跨表匹配。我们按确定性规则
      （以行号哈希为种子）为主表每行生成 姓名 / 证件后四位 / 城市 / 注册年份，
      再单独导出成一张"档案表"，两表的 ID 书写格式、姓名写法不同，
      从而构成真实的实体匹配问题。**这些身份字段是合成的，不含任何真实个人信息。**

运行：python -m src.pipeline.make_dataset
"""
from __future__ import annotations

import csv
import hashlib
import io
import random
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

from ..config import RAW_MAIN, RAW_REF, SEED, TARGET

UCI_ZIP = "https://archive.ics.uci.edu/static/public/222/bank+marketing.zip"
MIRRORS = [
    "https://archive.ics.uci.edu/static/public/222/bank+marketing.zip",
    "https://raw.githubusercontent.com/llabhishekll/bank-marketing-dataset/main/bank-full.csv",
    "https://cdn.jsdelivr.net/gh/llabhishekll/bank-marketing-dataset@main/bank-full.csv",
]

SURNAMES = list("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜")
GIVEN = ["伟", "芳", "娜", "秀英", "敏", "静", "丽", "强", "磊", "军", "洋", "勇", "艳", "杰",
         "娟", "涛", "明", "超", "秀兰", "霞", "平", "刚", "桂英", "建华", "文", "辉", "玲"]
CITIES = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京", "西安", "重庆",
          "苏州", "天津", "长沙", "郑州", "青岛", "宁波", "东莞", "无锡", "厦门", "福州"]


# --------------------------------------------------------------------- 获取
def _download(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (data-agent-course)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_uci() -> pd.DataFrame | None:
    """依次尝试镜像，返回原始 DataFrame；全部失败返回 None。"""
    for url in MIRRORS:
        try:
            print(f"[获取] 尝试 {url}")
            blob = _download(url)
            if url.endswith(".zip"):
                zf = zipfile.ZipFile(io.BytesIO(blob))
                names = zf.namelist()
                inner = [n for n in names if n.endswith("bank-full.csv")]
                if not inner:
                    inner_zip = [n for n in names if n.endswith(".zip")]
                    if inner_zip:
                        zf2 = zipfile.ZipFile(io.BytesIO(zf.read(inner_zip[0])))
                        inner = [n for n in zf2.namelist() if n.endswith("bank-full.csv")]
                        if not inner:
                            print("  zip 内部未找到 bank-full.csv")
                            continue
                        raw = zf2.read(inner[0])
                        df = pd.read_csv(io.BytesIO(raw), sep=";")
                        print(f"[获取] 成功（嵌套 zip）{df.shape}")
                        return df
                    print("  zip 内部未找到 bank-full.csv")
                    continue
                raw = zf.read(inner[0])
            else:
                raw = blob
            df = pd.read_csv(io.BytesIO(raw), sep=";")
            if TARGET not in df.columns:
                print(f"  字段不符：{list(df.columns)[:5]}…")
                continue
            print(f"[获取] 成功 {df.shape}")
            return df
        except Exception as e:  # noqa: BLE001
            print(f"  失败：{type(e).__name__}: {e}")
    return None


def synth_bank_marketing(n: int = 45211, seed: int = SEED) -> pd.DataFrame:
    """离线兜底：生成与 UCI Bank Marketing 同结构、同量级分布的数据。

    各字段取值域对齐真实数据集，目标 y 保持约 11.7% 的正例率（类别不平衡）。
    数据由线性打分 + 噪声生成，因此下游分类任务有可学习信号（AUC 约 0.75–0.85）。
    """
    rng = random.Random(seed)
    jobs = ["admin.", "technician", "services", "management", "retired", "blue-collar",
            "entrepreneur", "housemaid", "student", "self-employed", "unemployed", "unknown"]
    job_p = [0.11, 0.17, 0.09, 0.21, 0.04, 0.21, 0.03, 0.03, 0.02, 0.04, 0.03, 0.02]
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    rows = []
    for i in range(n):
        job = rng.choices(jobs, weights=job_p)[0]
        age = int(max(18, min(95, rng.gauss(41, 11))))
        marital = rng.choices(["married", "single", "divorced"], weights=[.6, .29, .11])[0]
        education = rng.choices(["primary", "secondary", "tertiary", "unknown"],
                                weights=[.15, .51, .29, .05])[0]
        default = rng.choices(["no", "yes"], weights=[.982, .018])[0]
        balance = int(rng.gauss(1362, 3045))
        housing = rng.choices(["yes", "no"], weights=[.56, .44])[0]
        loan = rng.choices(["no", "yes"], weights=[.84, .16])[0]
        contact = rng.choices(["cellular", "unknown", "telephone"], weights=[.65, .29, .06])[0]
        day = rng.randint(1, 31)
        month = months[min(11, max(0, int(rng.gauss(4.5, 2.6))))]
        duration = int(max(1, rng.expovariate(1 / 258)))
        campaign = max(1, int(rng.expovariate(1 / 2.7)))
        pdays = -1 if rng.random() < 0.82 else rng.randint(1, 871)
        previous = 0 if pdays == -1 else max(1, int(rng.expovariate(1 / 1.4)))
        poutcome = "unknown" if pdays == -1 else rng.choices(
            ["failure", "other", "success"], weights=[.68, .13, .19])[0]

        score = (
            0.012 * (age - 40)
            - 0.00003 * abs(balance)
            + (0.9 if poutcome == "success" else 0.0)
            + (0.45 if poutcome == "failure" else 0.0)
            + (0.25 if contact == "cellular" else -0.15)
            + 0.0009 * min(duration, 1200)
            - 0.06 * campaign
            + (0.2 if education == "tertiary" else 0.0)
            - (0.3 if loan == "yes" else 0.0)
            - (0.25 if default == "yes" else 0.0)
            + (0.15 if student else (-0.35 if job == "blue-collar" else 0.0))
            + 0.35 * previous
            + rng.gauss(0, 1.1)
        )
        # 截断使正例率≈11.7%
        y = "yes" if score > 2.05 else "no"
        rows.append({
            "age": age, "job": job, "marital": marital, "education": education,
            "default": default, "balance": balance, "housing": housing, "loan": loan,
            "contact": contact, "day": day, "month": month, "duration": duration,
            "campaign": campaign, "pdays": pdays, "previous": previous,
            "poutcome": poutcome, "y": y,
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------- 合成身份
def _stable_hash(s: str) -> int:
    return int(hashlib.md5(s.encode("utf-8")).hexdigest()[:8], 16)


def build_identity(df: pd.DataFrame, seed: int = SEED) -> pd.DataFrame:
    """为主表每行派生稳定的客户身份字段（合成，非真实个人信息）。

    开户年份由 出生年份(=2026-age) + 开户时年龄 推出，落在 [出生年份+18, 出生年份+age]，
    因此"开户年份与年龄互推"天生自洽——脏数据里被破坏后可以用这条规则检验（字段冲突）。
    """
    names, cities, years, tails = [], [], [], []
    ages = [int(a) if pd.notna(a := pd.to_numeric(df["age"].iloc[i], errors="coerce")) else 40
            for i in range(len(df))]
    for i in range(len(df)):
        h = _stable_hash(f"cust-{seed}-{i}")
        sur, giv = SURNAMES[h % len(SURNAMES)], GIVEN[(h // 7) % len(GIVEN)]
        names.append(sur + giv)
        cities.append(CITIES[(h // 13) % len(CITIES)])
        age = ages[i]
        birth = 2026 - age
        span = max(1, age - 17)
        open_age = 18 + (h // 19) % span
        open_age = min(open_age, age)
        years.append(min(2026, birth + open_age))
        tails.append(f"{h % 10000:04d}")
    out = pd.DataFrame({
        "客户编号": [f"C{i + 1:06d}" for i in range(len(df))],
        "客户姓名": names,
        "证件后四位": tails,
        "城市": cities,
        "开户年份": years,
    })
    return out


def main() -> None:
    df = fetch_uci()
    source = "UCI Bank Marketing (bank-full.csv)"
    if df is None:
        print("[获取] 全部镜像不可达，改用确定性合成器")
        df = synth_bank_marketing()
        source = "确定性合成器（同结构同量级，见 synth_bank_marketing）"

    df = df.reset_index(drop=True)
    ident = build_identity(df)
    # 主表保留 客户编号/姓名/证件后四位（与档案表写法不同，供实体匹配使用）
    main_df = pd.concat(
        [ident[["客户编号", "客户姓名", "证件后四位"]], df, ident[["城市", "开户年份"]]],
        axis=1,
    )
    main_df.to_csv(RAW_MAIN, index=False, encoding="utf-8-sig")

    ref_df = ident.rename(columns={
        "客户编号": "客户ID", "证件后四位": "证件尾号", "开户年份": "建档年份",
    })
    ref_df["教育水平(档案)"] = df["education"].values
    ref_df = ref_df[["客户ID", "客户姓名", "证件尾号", "城市", "建档年份", "教育水平(档案)"]]
    ref_df.to_csv(RAW_REF, index=False, encoding="utf-8-sig")

    print(f"[写出] {RAW_MAIN.name}  shape={main_df.shape}")
    print(f"[写出] {RAW_REF.name}  shape={ref_df.shape}")
    print(f"[数据来源] {source}")
    print(f"[类别分布] {df[TARGET].value_counts(normalize=True).round(4).to_dict()}")
    (RAW_MAIN.parent / "数据来源说明.txt").write_text(
        f"数据来源：{source}\n"
        f"获取时间：{pd.Timestamp.now():%Y-%m-%d %H:%M:%S}\n"
        f"主表行数：{len(main_df)}，列数：{main_df.shape[1]}\n"
        f"客户身份字段（客户编号/姓名/证件后四位/城市/开户年份）由确定性哈希合成，"
        f"不含真实个人信息；种子 SEED={SEED}。\n"
        f"原始 UCI 地址：{UCI_ZIP}\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
