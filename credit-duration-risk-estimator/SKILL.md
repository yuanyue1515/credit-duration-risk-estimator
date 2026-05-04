---
name: credit-duration-risk-estimator
description: Estimate duration and annualized risk for credit assets using a month-end roll-rate model. Use when Codex needs to work with MOB/vintage delinquency matrices, loan age buckets, balance forecasts, average balance, lifetime bad debt, annualized risk, lending bad rate, or requests based on the standard 12-period rolling methodology for loans, receivables, or segmented credit portfolios.
---

# Credit Duration Risk Estimator

## Overview

Use the month-end rolling methodology in [滚动率预估.md](/Users/dxm/Desktop/skill开发/年化风险/滚动率预估.md) as the primary business definition. Treat [月末滚动模版（加权平均）.ipynb](/Users/dxm/Desktop/skill开发/年化风险/月末滚动模版（加权平均）.ipynb) as the executable reference implementation when the user wants outputs aligned to the existing business notebook.

## Workflow

### 1. Normalize the request

Identify whether the request is for:

- A direct rerun or adaptation of the existing month-end notebook
- A new forecast under the same 12-period rolling logic
- A segmented estimate driven by configurable dimensions instead of fixed business fields
- A sensitivity run with modified coefficients, recovery ratio, or roll-rate overrides
- A simplified estimate without full MOB-by-loan-age raw data

If the user mentions “久期”“年化风险”“滚动率”“MOB19/M3+”“平均余额”，default to this rolling model, not a generic PD/LGD model.

### 2. Collect the minimum inputs

For the standard rolling model, prefer these data fields:

- `dt`: snapshot date
- `vntg_mth`: vintage month
- `loan_age_m`: loan age bucket, using `0..N` and `N+`
- `mob`: months on book, typically `0..20`
- `risk_inst`: MOB0 disbursement amount
- `risk_outsd`: outstanding balance used in the roll-rate matrix
- Optional business dimensions supplied through configuration, for example channel, project, customer segment, risk band, strategy tag, city tier, partner, or any custom field

Collect assumption inputs separately:

- Forecast start month and end month
- Product term count, for example `12`, `18`, `24`
- `mob_max`: forecast and output horizon. Default is `20`, which matches the common setup of `12` installment periods plus `8` overdue-age periods. In practice this should be adjusted from:
  `mob_max = 产品期数 + 最大逾期账龄`
  For example, if a product has `24` periods and you want to measure through `M8+`, then set `mob_max = 32`.
- `max_loan_age_bucket`: maximum finite loan-age bucket. Default `7`, which means the model uses `0..7` and `7+`. If the source table is `0..4` and `4+`, set this to `4`.
- `bad_debt_start_age`: first loan-age bucket included in bad-debt metrics. Default `4`, which matches the current `M3+` definition.
- Planned disbursement for months beyond observed data
- Dimension configuration:
  `group_by_fields`, `filter_dimensions`, `dimension_aliases`, `dimension_value_maps`
- Roll-rate estimation mode: weighted average or arithmetic average
- Adjustment coefficient by month
- tail recovery ratio
- Whether to use normal tail-bucket logic or “high-period fallback”
- Whether any single forecast cell should be manually overwritten

### 3. Apply estimation rules

Use the business rules below unless the user explicitly changes them:

- Use the standard 12-period average balance definition:
  `[(放款额 + MOB0余额)/4 + (MOB0余额 + MOB1余额)/2 + ... + (MOB(T-1)余额 + MOBT余额)/2] / (T + 0.5)`
  where `T = 产品期数`.
- For example:
  - `12`期产品：分母是 `12.5`，分子累计到 `(MOB11 + MOB12) / 2`
  - `18`期产品：分母是 `18.5`，分子累计到 `(MOB17 + MOB18) / 2`
- Compute duration as `产品期数 * 平均余额 / MOB0放款额`.
- Use `mob_max - 1` as the default bad-debt observation endpoint.
- Compute annualized risk as `MOB(mob_max - 1) 的坏账余额 / 平均余额`.
- Compute lifetime bad debt as `MOB(mob_max - 1) 的坏账余额`.
- Compute lending bad rate as `MOB(mob_max - 1) 的坏账余额 / MOB0放款额`.
- For example:
  - if `mob_max = 20`, observe `MOB19`
  - if `mob_max = 14`, observe `MOB13`
- Bad-debt buckets are configurable:
  - default `bad_debt_start_age = 4`, `max_loan_age_bucket = 7`, so bad debt is `4..7 + 7+`
  - if `max_loan_age_bucket = 4` and `bad_debt_start_age = 4`, bad debt is `4 + 4+`
- For `loan_age_m = 0`, compute real roll rates from `MOB0余额 / MOB0放款额` and then `MOBi余额 / MOB(i-1)余额`.
- Before any pivot or forecast step, clean invalid `loan_age_m / mob` combinations from the source table. Use the minimum valid `mob` implied by the bucket:
  `loan_age_m = 0 -> mob >= 0`,
  `loan_age_m = 1 -> mob >= 1`,
  ...,
  `loan_age_m = N -> mob >= N`,
  `loan_age_m = N+ -> mob >= N + 1`.
  Drop rows below the minimum valid `mob` instead of folding them into balances.
- For `loan_age_m = 1..N`, compute real roll rates as `当前账龄当前MOB余额 / 上一账龄上一MOB余额`.
- For predicted `loan_age_m = 1, MOB1`, use the recent 12 or 6 month weighted or arithmetic average and multiply by the current month adjustment coefficient at the forecast entry diagonal.
- For predicted `loan_age_m = 1, MOB>1`, propagate from the previous MOB using the stored multiplier ratio. Cap overflow values above `1` to `0.999`; the notebook currently also treats large values above `11` as invalid and resets them to `0.999`.
- For predicted `loan_age_m = 1`, use the multiplier-ratio path up to `产品期数 + 1`. From `产品期数 + 2` onward, use the tail factor rule:
  `final_rate1bei = 1.01`.
- For predicted `loan_age_m != 1`, use weighted average or arithmetic average after outlier handling. Do not force high-MOB tails upward; instead cap the computed arithmetic roll rate at `0.9999`.
- For arithmetic averages, use the skill’s sigma-style outlier removal: compute mean on the recent 18 valid samples, compute sample std with `n-1`, keep recent-12 values within `mean ± 1σ`, and for one multiplier path optionally clip values above `4`.
- For `loan_age_m = 0..N`, preserve any real observed balance or roll-rate cell already present in the source table. Only fill future missing cells by recursive prediction.
- For `N+`, default back to the notebook’s normal accumulation logic: keep the initialized `mob(N+1)` state, then from the next MOB onward use `(上月N+ + 上月账龄N) * 回收比例`, even if mature tail observations already exist in the source table.
- For the tail bucket, support both normal accumulation and high-period fallback. When the user does not specify, say which one you used.

### 4. Produce the core outputs

Always try to output:

- `平均余额`
- `久期`
- `预估年化风险`
- `全生命周期坏账`
- `放款不良率`
- Real roll-rate tables
- Predicted roll-rate tables
- Predicted balance tables by `loan_age_m`

If enough detail exists, also output:

- Filtered sub-portfolio results
- Results by configured dimension combination
- Manual override locations
- Weighted-average versus arithmetic-average comparison
- Normal tail-bucket accumulation versus high-period fallback comparison

### 5. Write conclusions, not just numbers

Summarize:

- Which months are forecasted rather than observed
- Which roll-rate cells are coefficient-driven rather than history-driven
- Whether the annualized risk changed because of disbursement plan, roll-rate tail, or tail-bucket recovery treatment
- Whether the output is directly notebook-aligned or only an approximation

## Output format

Use this structure in natural language or JSON-like bullets:

```text
Scope
- filter:
- start_dt:
- end_dt:
- forecast_months:

Assumptions
- rollrate_mode:
- adjustment_coefficient:
- tail_recovery_ratio:
- tail_logic:
- manual_overrides:

Results
- 平均余额:
- 久期:
- 预估年化风险:
- 全生命周期坏账:
- 放款不良率:

Diagnostics
- observed_vs_forecast_boundary:
- active_dimensions:
- sensitive_mobs:
- abnormal_cells:

Interpretation
- ...
```

## Execution guidance

Use [references/rolling_model.md](references/rolling_model.md) for the exact month-end rolling logic and metric definitions.

Use [references/data_contract.md](references/data_contract.md) to map raw fields into the notebook’s expected columns and configuration inputs.

If the user is preparing source data, point them first to [references/input_template.md](references/input_template.md) and [references/input_table_template.csv](references/input_table_template.csv).

Before filtering or grouping, normalize the segmentation config rather than hard-coding field names in logic:

- `dimension_aliases`: map business names such as “客群” or “渠道” to actual source fields
- `dimension_value_maps`: optionally merge source values into shared buckets
- `filter_dimensions`: define exact-match, multi-value, range, or callable-like business filters in a structured config
- `group_by_fields`: define which dimensions should appear in the output granularity

Use [月末滚动模版（加权平均）.ipynb](/Users/dxm/Desktop/skill开发/年化风险/月末滚动模版（加权平均）.ipynb) as the main business reference when the user wants outputs consistent with the existing business file.

Prefer [scripts/run_rollrate_notebook.py](scripts/run_rollrate_notebook.py) for deterministic reruns of the notebook logic, especially when you need:

- grouped reruns by a business dimension
- deterministic notebook-style reruns with the same recursive balance-filling behavior as the workbook

Before running, if the user does not specify `mob_max`, remind them that the default is `20` and ask whether they want to keep it or change it based on:

- product term count
- maximum delinquency age to be measured

Recommended prompt wording:

- 这次测算我会先确认四个关键参数：
  1. 产品期数
  2. 最大逾期账龄
  3. 最大有限账龄桶
  4. 坏账起算账龄
- 如果你不特别指定，我会使用主流默认口径：
  - 产品期数 `12`
  - 最长账龄 `7+`
  - 最大有限账龄桶 `7`
  - 坏账起算账龄 `4`
  - 对应 `mob_max = 12 + 8 = 20`
- 我会按：
  `mob_max = 产品期数 + 最大逾期账龄`
  来调整测算范围。
- 平均余额公式也会随产品期数同步变化：
  - `12`期产品：分母 `12.5`，分子累计到 `(MOB11 + MOB12) / 2`
  - `18`期产品：分母 `18.5`，分子累计到 `(MOB17 + MOB18) / 2`
  - 一般化后分母为 `产品期数 + 0.5`
- `loan_age_m = 1` 的尾部规则也会随产品期数变化：
  - `12`期产品：从 `mob14` 开始使用 `1.01`
  - `18`期产品：从 `mob20` 开始使用 `1.01`
  - 一般化后起点为 `产品期数 + 2`
- 全生命周期坏账、年化风险、放款不良率的观察终点也会随 `mob_max` 同步变化，默认使用 `mob_max - 1` 对应时点的 `M3+`。
- 如果用户的账龄结构不是 `0..7 + 7+`，而是例如 `0..4 + 4+`，则需要同步设置：
  - `max_loan_age_bucket = 4`
  - 若坏账定义仍为 `M3+`，则 `bad_debt_start_age = 4`
- 在这种情况下，观察终点坏账会按 `4 + 4+` 统计，而不是 `4..7 + 7+`。
- 如果 `mob_max` 设置得更大，你需要提供更多历史样本；至少要保证样本覆盖的放款月数不小于 `mob_max`，否则高 MOB 区间的滚动率和余额预测会不稳定。

Use [scripts/estimate_credit_risk.py](scripts/estimate_credit_risk.py) only as a simplified fallback when the user asks for a quick approximation and does not have full roll-rate source data.

## Guardrails

- Do not silently replace the notebook’s business formula with a finance-textbook duration or PD/LGD expected loss formula.
- Do not treat `M3+` as a generic default measure without stating the business definition being used.
- Do not mix observed balances and forecast balances without marking the cutoff month.
- Do not assume the same result under weighted-average and arithmetic-average roll-rate modes; present the selected mode explicitly.
- Do not overwrite single-cell roll-rate adjustments unless the user asked for it or the notebook already contains that override.
- If the source data lacks `risk_inst` or `risk_outsd`, say the standard rolling model cannot be reproduced exactly.
- Do not hard-code segmentation logic to `loan_belong_tag`, `project_total`, or any current dataset-specific field when a configurable dimension schema is available.

## Example triggers

Typical requests that should trigger this skill:

- "帮我按 month end 滚动率模型预测久期和年化风险。"
- "按现有 notebook 口径，给端外生态团队重算 MOB19 的 M3+ 风险。"
- "分客群看滚动率预估、平均余额和放款不良率。"
- "我想改尾部回收比例和调整系数，比较年化风险变化。"
