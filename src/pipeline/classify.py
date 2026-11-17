"""下游分类任务：同一套建模流程，分别跑"脏数据基线"和"智能体处理后"。

为了让对比公平，两次建模共用同一个函数：
- 相同随机种子、相同分层切分、相同特征编码规则；
- 唯一区别是**喂进去的数据**（脏 vs 清洗后）；
- 每个数据集内部按 80/20 分层留出测试集，测试集不参与任何编码/填补统计。

不平衡处理放在训练侧：类别权重 + 阈值选择，验证集分布不被破坏。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from ..config import SEED, TARGET
from ..tools.profiling import parse_number

ID_COLS = {"客户编号", "客户姓名", "证件后四位", "客户ID", "证件尾号"}
LEAK_COLS = {"开户年份"}          # 与年龄互推，作为特征会造成信息泄漏


def _to_binary_y(s: pd.Series) -> pd.Series:
    t = s.astype(str).str.strip().str.lower()
    pos = {"yes", "1", "y", "true", "是"}
    neg = {"no", "0", "n", "false", "否"}
    out = pd.Series(np.nan, index=s.index, dtype="float64")
    out[t.isin(pos)] = 1.0
    out[t.isin(neg)] = 0.0
    return out


def prepare_features(train: pd.DataFrame, test: pd.DataFrame, target: str = TARGET):
    """只用训练集统计量做编码与填补，再映射到测试集。"""
    feat_cols = [c for c in train.columns if c != target and c not in ID_COLS | LEAK_COLS]
    num_cols, cat_cols = [], []
    for c in feat_cols:
        nums = pd.Series([parse_number(v) for v in train[c].tolist()], dtype="float64")
        if nums.notna().mean() > 0.6:
            num_cols.append(c)
        else:
            cat_cols.append(c)

    Xtr = pd.DataFrame(index=train.index)
    Xte = pd.DataFrame(index=test.index)
    for c in num_cols:
        a = pd.Series([parse_number(v) for v in train[c].tolist()], index=train.index, dtype="float64")
        b = pd.Series([parse_number(v) for v in test[c].tolist()], index=test.index, dtype="float64")
        med = a.median()
        Xtr[c] = a.fillna(med)
        Xte[c] = b.fillna(med)
    for c in cat_cols:
        a = train[c].astype(str).str.strip().str.lower().replace({"nan": ""})
        keep = a.value_counts().head(20).index
        Xtr[c] = a.where(a.isin(keep), "__other__")
        b = test[c].astype(str).str.strip().str.lower().replace({"nan": ""})
        Xte[c] = b.where(b.isin(keep), "__other__")

    if cat_cols:
        dummy_tr = pd.get_dummies(Xtr[cat_cols], prefix=cat_cols, dummy_na=False)
        dummy_te = pd.get_dummies(Xte[cat_cols], prefix=cat_cols, dummy_na=False)
        dummy_te = dummy_te.reindex(columns=dummy_tr.columns, fill_value=0)
        Xtr = pd.concat([Xtr[num_cols], dummy_tr], axis=1)
        Xte = pd.concat([Xte[num_cols], dummy_te], axis=1)

    Xtr = Xtr.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    Xte = Xte.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return Xtr.astype(float), Xte.astype(float)


def _metrics(y_true: np.ndarray, y_pred: np.ndarray, y_prob: np.ndarray) -> dict:
    from sklearn.metrics import (accuracy_score, confusion_matrix, f1_score,
                                 precision_score, recall_score, roc_auc_score)
    return {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "precision": round(float(precision_score(y_true, y_pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, y_pred, zero_division=0)), 4),
        "f1": round(float(f1_score(y_true, y_pred, zero_division=0)), 4),
        "roc_auc": round(float(roc_auc_score(y_true, y_prob)), 4),
        "confusion_matrix": confusion_matrix(y_true, y_pred).tolist(),
        "n_test": int(len(y_true)),
        "pos_rate_test": round(float(y_true.mean()), 4),
    }


def train_and_evaluate(df: pd.DataFrame, label: str = "数据集",
                       target: str = TARGET, seed: int = SEED,
                       n_splits: int = 5, max_rows: int | None = None) -> dict:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold, train_test_split

    data = df.copy()
    if max_rows and len(data) > max_rows:
        # 分层抽样以控制运行时间，仍保持类别比例
        data = (data.groupby(data[target].astype(str), group_keys=False)
                    .apply(lambda g: g.sample(min(len(g), max(1, int(max_rows * len(g) / len(df)))),
                                              random_state=seed)))
    y_all = _to_binary_y(data[target])
    ok = y_all.notna()
    data, y_all = data[ok], y_all[ok]
    if y_all.nunique() < 2 or len(data) < 50:
        return {"label": label, "error": "有效样本不足或类别单一", "n_rows": int(len(data))}

    X_tr, X_te, y_tr, y_te = train_test_split(
        data, y_all.astype(int), test_size=0.2, stratify=y_all.astype(int), random_state=seed)
    Xtr, Xte = prepare_features(X_tr, X_te, target=target)

    pos, neg = int((y_tr == 1).sum()), int((y_tr == 0).sum())
    cw = {0: 1.0, 1: float(neg) / max(pos, 1)}
    sw = np.where(y_tr == 1, cw[1], 1.0)

    candidates = {
        "逻辑回归(带类别权重)": LogisticRegression(
            max_iter=2000, class_weight=cw, random_state=seed),
        "梯度提升树": HistGradientBoostingClassifier(
            max_iter=300, learning_rate=0.06, random_state=seed),
    }
    results: dict[str, dict] = {}
    best_name, best_f1 = None, -1.0
    best_prob = None

    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    for name, model in candidates.items():
        cv_f1 = []
        for tr, va in skf.split(Xtr, y_tr):
            m = _clone_model(model, name, seed, cw)
            if name == "梯度提升树":
                m.fit(Xtr.iloc[tr], y_tr.iloc[tr], sample_weight=sw[tr])
            else:
                m.fit(Xtr.iloc[tr], y_tr.iloc[tr])
            p = m.predict_proba(Xtr.iloc[va])[:, 1]
            pr = (p >= 0.5).astype(int)
            from sklearn.metrics import f1_score
            cv_f1.append(f1_score(y_tr.iloc[va], pr, zero_division=0))
        # 全训练集重训 + 测试集评估
        m = _clone_model(model, name, seed, cw)
        if name == "梯度提升树":
            m.fit(Xtr, y_tr, sample_weight=sw)
        else:
            m.fit(Xtr, y_tr)
        prob = m.predict_proba(Xte)[:, 1]

        # 阈值在训练集上选（按 F1），不偷看测试集
        if name == "梯度提升树":
            m.fit(Xtr, y_tr, sample_weight=sw)
        tr_prob = m.predict_proba(Xtr)[:, 1]
        thr, thr_f1 = 0.5, -1
        for t in np.arange(0.1, 0.91, 0.02):
            from sklearn.metrics import f1_score as _f1
            f = _f1(y_tr, (tr_prob >= t).astype(int), zero_division=0)
            if f > thr_f1:
                thr, thr_f1 = float(t), f

        pred = (prob >= thr).astype(int)
        met = _metrics(y_te.values, pred, prob)
        met["cv_f1_mean"] = round(float(np.mean(cv_f1)), 4)
        met["cv_f1_std"] = round(float(np.std(cv_f1)), 4)
        met["threshold"] = round(thr, 2)
        results[name] = met
        if met["cv_f1_mean"] > best_f1:
            best_f1, best_name, best_prob = met["cv_f1_mean"], name, prob

    return {
        "label": label,
        "n_rows": int(len(data)),
        "n_features": int(Xtr.shape[1]),
        "pos_rate": round(float(y_all.mean()), 4),
        "models": results,
        "best_model": best_name,
        "best": results[best_name],
        "test_probs": best_prob,
        "test_y": y_te.values,
    }


def _clone_model(model, name: str, seed: int, cw: dict):
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    if name == "梯度提升树":
        return HistGradientBoostingClassifier(max_iter=300, learning_rate=0.06, random_state=seed)
    # 逻辑回归对特征尺度敏感：金额/时长量级差几个数量级，不标准化会不收敛
    return Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(max_iter=3000, class_weight=cw, random_state=seed)),
    ])


def compare(before: pd.DataFrame, after: pd.DataFrame, seed: int = SEED,
            precise_only: pd.DataFrame | None = None) -> dict:
    """脏数据基线 vs 智能体处理后（可选：仅做“可精确还原修复”的中间档对照）。"""
    a = train_and_evaluate(before, label="脏数据（未经智能体处理）", seed=seed)
    b = train_and_evaluate(after, label="智能体处理后", seed=seed)
    out = {
        "before": {k: v for k, v in a.items() if k not in ("test_probs", "test_y")},
        "after": {k: v for k, v in b.items() if k not in ("test_probs", "test_y")},
        "delta": {
            k: round(b["best"][k] - a["best"][k], 4)
            for k in ("accuracy", "precision", "recall", "f1", "roc_auc")
            if k in a.get("best", {}) and k in b.get("best", {})
        },
    }
    if precise_only is not None:
        c = train_and_evaluate(precise_only, label="仅精确修复（格式+重复，不含填补/裁剪）", seed=seed)
        out["precise_only"] = {k: v for k, v in c.items() if k not in ("test_probs", "test_y")}
        out["delta_precise"] = {
            k: round(c["best"][k] - a["best"][k], 4)
            for k in ("accuracy", "precision", "recall", "f1", "roc_auc")
            if k in a.get("best", {}) and k in c.get("best", {})
        }
        out["delta_after_vs_precise"] = {
            k: round(b["best"][k] - c["best"][k], 4)
            for k in ("accuracy", "precision", "recall", "f1", "roc_auc")
            if k in c.get("best", {}) and k in b.get("best", {})
        }
    return out
