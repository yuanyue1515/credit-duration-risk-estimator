# Month-End Rolling Model

## Business objective

Estimate future balances and risk metrics for each vintage month using a month-end roll-rate matrix with loan age buckets `0..N` and `N+`.

## Metric definitions

- Average balance:
  `[(放款额 + MOB0余额)/4 + (MOB0余额 + MOB1余额)/2 + ... + (MOB11余额 + MOB12余额)/2] / 12.5`
- Duration:
  `12 * 平均余额 / MOB0放款额`
- Estimated annualized risk:
  `MOB(mob_max - 1) 的坏账余额 / 平均余额`
- Lifetime bad debt:
  `MOB(mob_max - 1) 的坏账余额`
- Lending bad rate:
  `MOB(mob_max - 1) 的坏账余额 / MOB0放款额`

Bad-debt buckets are configurable. In the default notebook-aligned setup, bad debt is the sum of forecast balances at `loan_age_m = 4..7` plus `7+` on `mob = 19`.

## Real roll-rate logic

### Loan age 0

- `MOB0 roll rate = MOB0余额 / MOB0放款额`
- `MOBi roll rate = MOBi余额 / MOB(i-1)余额`

### Loan age 1..N

- `当前账龄 i, 当前MOB j 的滚动率 = 当前账龄 i, 当前MOB j 余额 / 上一账龄 i-1, 上一MOB j-1 余额`

## Forecast roll-rate logic

### Loan age 1

- `MOB1` uses recent-window weighted average or arithmetic average, then multiplies the diagonal entry month by the adjustment coefficient.
- `MOB > 1` uses a multiplier:
  `MOBi倍率 = MOBi滚动率 / MOB(i-1)滚动率`
- Forecast path:
  `当前月 MOBi滚动率 = 当前月 MOB(i-1)滚动率 * MOBi倍率`
- On the first forecast diagonal, also multiply by the adjustment coefficient.
- Cap implausible outputs above `1` to `0.999`.

### Loan age 2..N

- Weighted average mode:
  use recent-window roll rates weighted by the corresponding balance.
- Arithmetic average mode:
  use recent-window filtered averages after outlier removal.
- Tail treatment:
  do not force `mob > 16` upward; use the computed roll rate and cap it at `0.9999`.

## Outlier handling

The skill does not implement a clean `3 sigma` rule. It uses a pragmatic recent-window filter:

- compute mean on roughly the recent `18` observations
- compute sample std on the same window using `n - 1`
- keep values in the recent `12` observations within `mean ± 1.0 * std`
- for one multiplier path, additionally clip values above `4`
- if the filtered mean is missing, fall back to the broader mean
- cap the final arithmetic average at `0.9999`

Keep this implementation detail explicit when reproducing results.

## Balance forecast logic

### Loan age 0

- Preserve real observed `loan_age_m = 0` cells whenever they exist.
- Only fill missing future cells with recursive forecast values.
- First forecasted MOB0 balance:
  `当前月MOB0余额 = 当前月MOB0放款额 * MOB0滚动率预测值`
- Later MOBs:
  `当前月MOBi余额 = 当前月MOB(i-1)余额 * MOBi滚动率预测值`

### Loan age 1..N

- Preserve real observed cells for `loan_age_m = 1..7` whenever they exist.
- Only fill missing future cells with recursive forecast values.
- `当前月账龄 i, MOB j 余额 = 当前月账龄 i-1, MOB j-1 余额 * 当前月账龄 i, MOB j 滚动率预测值`

### Loan age N+

Normal logic:

- Keep the initialized `mob(N+1)` state.
- For later MOBs, use:
  `N+余额 = (账龄N的上月余额 + N+的上月余额) * 回收比例`
- This follows the notebook’s recursive accumulation behavior.

High-period fallback:

- Replace forecasted `账龄N` with lagged `账龄N-1`
- Then apply the same tail-bucket accumulation formula

## Operational cautions

- The notebook is hard-coded for `mob <= 20` and metric readout on `mob = 19`.
- Several forecast diagonals depend on index arithmetic like `n - i - 1` or `n - i - 2`; preserve these offsets if reproducing the original workbook behavior.
- If the latest month is not month-end, report that the result uses the raw observed snapshot date directly without day-count extrapolation.
- Filter logic, adjustment coefficients, and tail recovery ratio materially change outputs. Report them every time.
