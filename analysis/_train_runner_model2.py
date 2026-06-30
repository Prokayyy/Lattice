"""Runner model on candle-labeled enriched subset (May 23-31). ~/ml-venv/bin/python."""
import json
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

alerts = json.load(open('analysis/_alerts_dataset_enriched.json'))
rows = []
for a in alerts:
    if not a.get('candle_6h_max'):
        continue
    r = {
        'token': a['token_address'], 'ts': a['alert_timestamp'],
        'y': 1 if a['candle_6h_max'] >= 2.0 else 0,
        'y24': (1 if (a.get('candle_24h_max') or 0) >= 2.0 else 0) if a.get('candle_24h_max') else None,
        'loser': 1 if a['candle_6h_max'] < 1.2 else 0,
        'score': a.get('score'), 'raw_score': a.get('raw_score'), 'penalty': a.get('penalty'),
        'fdv': a.get('alert_fdv'), 'liq': a.get('alert_liquidity'),
        'pressure': a.get('alert_pressure'), 'impulse': a.get('alert_impulse'),
        'route': a.get('alert_route') or 'unknown', 'qtag': a.get('quality_tag') or 'unknown',
    }
    for k, v in (a.get('snap') or {}).items():
        if isinstance(v, (int, float)) and k != 'snapshot_timestamp':
            r[f's_{k}'] = v
    for k, v in ((a.get('traj') or {}) if isinstance(a.get('traj'), dict) else {}).items():
        if isinstance(v, (int, float)):
            r[f't_{k}'] = v
    rows.append(r)

df = pd.DataFrame(rows).sort_values('ts')
df = df.drop_duplicates(subset='token', keep='first').reset_index(drop=True)
df['log_fdv'] = np.log1p(df.fdv.fillna(0))
df['log_liq'] = np.log1p(df.liq.fillna(0))
print(f'candle-labeled deduped: n={len(df)}, runners: {df.y.sum()} ({df.y.mean():.1%}), losers: {df.loser.mean():.1%}')

BASE = ['score', 'raw_score', 'penalty', 'log_fdv', 'log_liq', 'pressure', 'impulse']
SNAP = [c for c in df.columns if c.startswith(('s_', 't_')) and df[c].notna().sum() > len(df) * 0.4]
print(f'snapshot features used: {len(SNAP)}')
route_d = pd.get_dummies(df.route, prefix='r')
qtag_d = pd.get_dummies(df.qtag, prefix='q')


def matrix(cols, cats=True):
    X = df[cols].copy()
    if cats:
        X = pd.concat([X, route_d, qtag_d], axis=1)
    miss = X.isna()
    for c in X.columns[miss.any()]:
        X[f'm_{c}'] = miss[c].astype(int)
    return X.fillna(X.median(numeric_only=True)).fillna(0).astype(float)


def evaluate(X, y, name):
    n = len(X)
    cut = int(0.8 * n)
    models = {
        'logreg': lambda: make_pipeline(StandardScaler(), LogisticRegression(
            class_weight='balanced', max_iter=2000, C=0.1)),
        'hgb': lambda: HistGradientBoostingClassifier(
            max_depth=3, learning_rate=0.05, max_iter=300, min_samples_leaf=25,
            l2_regularization=1.0, random_state=42),
        'rf': lambda: RandomForestClassifier(
            n_estimators=400, min_samples_leaf=15, class_weight='balanced',
            random_state=42, n_jobs=-1),
    }
    print(f'\n=== {name}: n={n}, pos={int(y.sum())} ({y.mean():.1%}) ===')
    out = {}
    tscv = TimeSeriesSplit(n_splits=5)
    for mname, mk in models.items():
        aucs, prs, oof_i, oof_p = [], [], [], []
        for tr, te in tscv.split(X):
            m = mk()
            m.fit(X.iloc[tr], y.iloc[tr])
            p = m.predict_proba(X.iloc[te])[:, 1]
            if y.iloc[te].nunique() == 2:
                aucs.append(roc_auc_score(y.iloc[te], p))
                prs.append(average_precision_score(y.iloc[te], p))
            oof_i.extend(te)
            oof_p.extend(p)
        m = mk()
        m.fit(X.iloc[:cut], y.iloc[:cut])
        ph = m.predict_proba(X.iloc[cut:])[:, 1]
        ha = roc_auc_score(y.iloc[cut:], ph) if y.iloc[cut:].nunique() == 2 else float('nan')
        print(f'{mname:>8}: CV AUC {np.mean(aucs):.3f}±{np.std(aucs):.3f}  PR {np.mean(prs):.3f}±{np.std(prs):.3f} | holdout AUC {ha:.3f}')
        out[mname] = (np.mean(aucs), np.array(oof_i), np.array(oof_p), mk)
    for bn, col in [('score-only', 'score'), ('fdv-only', 'log_fdv'), ('liq-only', 'log_liq')]:
        v = X[col] if col in X else df[col]
        aucs = [roc_auc_score(y.iloc[te], v.iloc[te]) for tr, te in tscv.split(X) if y.iloc[te].nunique() == 2]
        hb = roc_auc_score(y.iloc[cut:], v.iloc[cut:]) if y.iloc[cut:].nunique() == 2 else float('nan')
        print(f'{bn:>10} baseline: CV {np.mean(aucs):.3f}±{np.std(aucs):.3f} | holdout {hb:.3f}')
    best = max(out, key=lambda k: out[k][0])
    print(f'best: {best}')
    return out[best], best


y = df['y']
(resA, bestA) = evaluate(matrix(BASE), y, 'BASE features (candle 6h label)')
XB = matrix(BASE + SNAP)
(resB, bestB) = evaluate(XB, y, 'BASE+SNAP+XF features (candle 6h label)')

# operating points on best of B
_, oidx, opred, mkB = resB
yo = y.iloc[oidx].values
lo = df['loser'].iloc[oidx].values
order = np.argsort(-opred)
yo_s, lo_s, pr_s = yo[order], lo[order], opred[order]
print('\n=== operating points (B OOF) ===')
print(f'{"kept%":>6}{"thresh":>8}{"recall":>8}{"precision":>10}{"losers_excl%":>13}')
for kept in (0.1, 0.2, 0.3, 0.4, 0.5, 0.7):
    k = int(kept * len(yo_s))
    if k < 1:
        continue
    print(f'{kept:>6.0%}{pr_s[k-1]:>8.3f}{yo_s[:k].sum()/max(yo.sum(),1):>8.1%}{yo_s[:k].mean():>10.1%}'
          f'{1-lo_s[:k].sum()/max(lo.sum(),1):>13.1%}')

# permutation importance on holdout
cut = int(0.8 * len(XB))
m = mkB()
m.fit(XB.iloc[:cut], y.iloc[:cut])
pi = permutation_importance(m, XB.iloc[cut:], y.iloc[cut:], n_repeats=25, random_state=42, scoring='roc_auc')
imp = pd.Series(pi.importances_mean, index=XB.columns).sort_values(ascending=False)
print('\ntop-15 permutation importance (holdout):')
print(imp.head(15).to_string())

# interpretable tree
tree = DecisionTreeClassifier(max_depth=3, min_samples_leaf=25, class_weight='balanced', random_state=42)
tree.fit(XB.iloc[:cut], y.iloc[:cut])
pt = tree.predict_proba(XB.iloc[cut:])[:, 1]
print(f'\ndepth-3 tree holdout AUC: {roc_auc_score(y.iloc[cut:], pt):.3f}')
print(export_text(tree, feature_names=list(XB.columns), max_depth=3))

# 24h label variant on B features
m24 = df['y24'].notna()
y24 = df.loc[m24, 'y24'].astype(int).reset_index(drop=True)
X24 = XB.loc[m24].reset_index(drop=True)
evaluate(X24, y24, 'BASE+SNAP, 24h-horizon label')
