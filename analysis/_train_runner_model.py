"""Train >=2x runner classifiers on alert dataset. Run with ~/ml-venv/bin/python."""
import json
import math
import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier, export_text

warnings.filterwarnings('ignore')
rng = np.random.RandomState(42)

alerts = json.load(open('analysis/_alerts_dataset_enriched.json'))

rows = []
for a in alerts:
    w = a['windows'].get('6h')
    if not w or not w.get('complete') or w.get('max_multiple') is None:
        continue
    r = {
        'token': a['token_address'],
        'ts': a['alert_timestamp'],
        'y': 1 if w['max_multiple'] >= 2.0 else 0,
        'loser': 1 if w['max_multiple'] < 1.2 else 0,
        'score': a.get('score'),
        'raw_score': a.get('raw_score'),
        'penalty': a.get('penalty'),
        'fdv': a.get('alert_fdv'),
        'liq': a.get('alert_liquidity'),
        'pressure': a.get('alert_pressure'),
        'impulse': a.get('alert_impulse'),
        'route': a.get('alert_route') or 'unknown',
        'qtag': a.get('quality_tag') or 'unknown',
    }
    snap = a.get('snap') or {}
    for k, v in snap.items():
        if isinstance(v, (int, float)) and k not in ('snapshot_timestamp',):
            r[f's_{k}'] = v
    traj = a.get('traj') or {}
    for k, v in (traj.items() if isinstance(traj, dict) else []):
        if isinstance(v, (int, float)):
            r[f't_{k}'] = v
    rows.append(r)

df = pd.DataFrame(rows).sort_values('ts').reset_index(drop=True)
print(f'complete-6h alerts: {len(df)}; runners: {df.y.sum()} ({df.y.mean():.1%}); losers: {df.loser.mean():.1%}')

# dedup: first alert per token
df_d = df.drop_duplicates(subset='token', keep='first').reset_index(drop=True)
print(f'after first-alert-per-token dedup: {len(df_d)}; runners: {df_d.y.sum()} ({df_d.y.mean():.1%})')

df_d['log_fdv'] = np.log1p(df_d.fdv.fillna(0))
df_d['log_liq'] = np.log1p(df_d.liq.fillna(0))

BASE = ['score', 'raw_score', 'penalty', 'log_fdv', 'log_liq', 'pressure', 'impulse']
route_d = pd.get_dummies(df_d.route, prefix='r')
qtag_d = pd.get_dummies(df_d.qtag, prefix='q')
SNAP = [c for c in df_d.columns if c.startswith(('s_', 't_'))]


def matrix(cols, with_cats=True):
    X = df_d[cols].copy()
    if with_cats:
        X = pd.concat([X, route_d, qtag_d], axis=1)
    miss = X.isna()
    for c in X.columns[miss.any()]:
        X[f'm_{c}'] = miss[c].astype(int)
    X = X.fillna(X.median(numeric_only=True)).fillna(0)
    return X.astype(float)


def evaluate(X, y, ts, name):
    """Time-ordered CV + last-20% holdout. Returns oof preds for best model."""
    n = len(X)
    cut = int(0.8 * n)
    models = {
        'logreg': lambda: make_pipeline(StandardScaler(), LogisticRegression(
            class_weight='balanced', max_iter=2000, C=0.1)),
        'hgb': lambda: HistGradientBoostingClassifier(
            max_depth=3, learning_rate=0.05, max_iter=300,
            min_samples_leaf=25, l2_regularization=1.0, random_state=42),
        'rf': lambda: RandomForestClassifier(
            n_estimators=400, min_samples_leaf=20, class_weight='balanced',
            random_state=42, n_jobs=-1),
    }
    print(f'\n=== {name}: n={n}, pos={int(y.sum())} ({y.mean():.1%}) ===')
    results = {}
    oof_store = {}
    tscv = TimeSeriesSplit(n_splits=5)
    for mname, mk in models.items():
        aucs, praucs = [], []
        oof_idx, oof_pred = [], []
        for tr, te in tscv.split(X):
            m = mk()
            m.fit(X.iloc[tr], y.iloc[tr])
            p = m.predict_proba(X.iloc[te])[:, 1]
            if y.iloc[te].nunique() == 2:
                aucs.append(roc_auc_score(y.iloc[te], p))
                praucs.append(average_precision_score(y.iloc[te], p))
            oof_idx.extend(te)
            oof_pred.extend(p)
        m = mk()
        m.fit(X.iloc[:cut], y.iloc[:cut])
        ph = m.predict_proba(X.iloc[cut:])[:, 1]
        hauc = roc_auc_score(y.iloc[cut:], ph) if y.iloc[cut:].nunique() == 2 else float('nan')
        hpr = average_precision_score(y.iloc[cut:], ph) if y.iloc[cut:].nunique() == 2 else float('nan')
        results[mname] = (np.mean(aucs), np.std(aucs), hauc)
        oof_store[mname] = (np.array(oof_idx), np.array(oof_pred))
        print(f'{mname:>8}: CV AUC {np.mean(aucs):.3f}±{np.std(aucs):.3f}  PR-AUC {np.mean(praucs):.3f}±{np.std(praucs):.3f}  | holdout AUC {hauc:.3f} PR {hpr:.3f}')
    # baselines
    for bname, col, sign in [('score-only', 'score', 1), ('fdv-only', 'log_fdv', 1)]:
        v = X[col] * sign
        aucs = []
        for tr, te in tscv.split(X):
            if y.iloc[te].nunique() == 2:
                aucs.append(roc_auc_score(y.iloc[te], v.iloc[te]))
        hb = roc_auc_score(y.iloc[cut:], v.iloc[cut:]) if y.iloc[cut:].nunique() == 2 else float('nan')
        print(f'{bname:>10} baseline: CV AUC {np.mean(aucs):.3f}±{np.std(aucs):.3f} | holdout {hb:.3f}')
    best = max(results, key=lambda k: results[k][0])
    print(f'best by CV: {best}')
    return best, oof_store[best], results, models[best]


y = df_d['y']
ts = df_d['ts']

# ---- dataset A: base features, all complete alerts ----
XA = matrix(BASE)
bestA, (oidxA, opredA), resA, mkA = evaluate(XA, y, ts, 'A: base alert features (full period)')

# ---- dataset B: base + snapshot features, enriched subset ----
# dedup WITHIN the enriched subset (first enriched alert per token), since the
# global first-alert dedup above discards late-May rows that carry snap features.
snapcols = [c for c in df.columns if c.startswith(('s_', 't_'))]
dfe = df[df[snapcols].notna().any(axis=1)] if snapcols else df.iloc[0:0]
dfe = dfe.drop_duplicates(subset='token', keep='first').reset_index(drop=True)
print(f'\nenriched subset (complete-6h, dedup-within): {len(dfe)}; runners: {dfe.y.sum()}')
if len(dfe) > 100:
    dfe['log_fdv'] = np.log1p(dfe.fdv.fillna(0))
    dfe['log_liq'] = np.log1p(dfe.liq.fillna(0))
    route_e = pd.get_dummies(dfe.route, prefix='r')
    qtag_e = pd.get_dummies(dfe.qtag, prefix='q')

    def matrix_e(cols, with_cats=True):
        X = dfe[cols].copy()
        if with_cats:
            X = pd.concat([X, route_e, qtag_e], axis=1)
        miss = X.isna()
        for c in X.columns[miss.any()]:
            X[f'm_{c}'] = miss[c].astype(int)
        X = X.fillna(X.median(numeric_only=True)).fillna(0)
        return X.astype(float)

    SNAP_E = [c for c in snapcols if dfe[c].notna().sum() > 50]
    yB = dfe['y']
    evaluate(matrix_e(BASE), yB, dfe['ts'], 'A-on-B-subset: base features, enriched period only')
    XB = matrix_e(BASE + SNAP_E)
    bestB, (oidxB, opredB), resB, mkB = evaluate(XB, yB, dfe['ts'], 'B: base+snapshot+xf features (May23-31)')
else:
    print('enriched subset too small, skipping B')
    bestB = None

# ---- operating points on pooled OOF (dataset A, best model) ----
print('\n=== operating points (dataset A OOF, best model) ===')
yo = y.iloc[oidxA].values
lo = df_d['loser'].iloc[oidxA].values
order = np.argsort(-opredA)
yo_s, lo_s, pr_s = yo[order], lo[order], opredA[order]
total_r, total_l = yo.sum(), lo.sum()
print(f'{"kept%":>6}{"thresh":>8}{"recall":>8}{"precision":>10}{"losers_excl%":>13}')
for kept in (0.1, 0.2, 0.3, 0.4, 0.5, 0.7):
    k = int(kept * len(yo_s))
    if k < 1:
        continue
    rec = yo_s[:k].sum() / total_r
    prec = yo_s[:k].mean()
    lex = 1 - lo_s[:k].sum() / total_l
    print(f'{kept:>6.0%}{pr_s[k-1]:>8.3f}{rec:>8.1%}{prec:>10.1%}{lex:>13.1%}')

# ---- permutation importance (holdout, dataset A) ----
cut = int(0.8 * len(XA))
m = mkA()
m.fit(XA.iloc[:cut], y.iloc[:cut])
pi = permutation_importance(m, XA.iloc[cut:], y.iloc[cut:], n_repeats=20,
                            random_state=42, scoring='roc_auc')
imp = pd.Series(pi.importances_mean, index=XA.columns).sort_values(ascending=False)
print('\ntop-12 permutation importance (A, holdout):')
print(imp.head(12).to_string())

if bestB:
    yB = yB.reset_index(drop=True)
    cutB = int(0.8 * len(XB))
    mB = mkB()
    mB.fit(XB.iloc[:cutB], yB.iloc[:cutB])
    piB = permutation_importance(mB, XB.iloc[cutB:], yB.iloc[cutB:], n_repeats=20,
                                 random_state=42, scoring='roc_auc')
    impB = pd.Series(piB.importances_mean, index=XB.columns).sort_values(ascending=False)
    print('\ntop-15 permutation importance (B, holdout):')
    print(impB.head(15).to_string())

# ---- interpretable tree ----
print('\n=== depth-3 decision tree (dataset A, train=first 80%) ===')
tree = DecisionTreeClassifier(max_depth=3, min_samples_leaf=30, class_weight='balanced', random_state=42)
tree.fit(XA.iloc[:cut], y.iloc[:cut])
pt = tree.predict_proba(XA.iloc[cut:])[:, 1]
print(f'tree holdout AUC: {roc_auc_score(y.iloc[cut:], pt):.3f}')
print(export_text(tree, feature_names=list(XA.columns), max_depth=3))

json.dump({
    'n_complete_6h': len(df), 'n_dedup': len(df_d),
    'base_rate': float(df_d.y.mean()),
    'results_A': {k: [float(x) for x in v] for k, v in resA.items()},
    'results_B': ({k: [float(x) for x in v] for k, v in resB.items()} if bestB else None),
    'top_importance_A': {k: float(v) for k, v in imp.head(12).items()},
}, open('analysis/_runner_model_results.json', 'w'), indent=1)
print('\nsaved analysis/_runner_model_results.json')
