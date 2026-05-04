# Input Template

## What to provide

For the standard month-end rolling model, provide one flat CSV table. Each row should represent one aggregated balance cell under a given:

- snapshot date `dt`
- vintage month `vntg_mth`
- loan age bucket `loan_age_m`
- `mob`

The file template is:

- [input_table_template.csv](input_table_template.csv)

## Required columns

| column | required | type | meaning |
| --- | --- | --- | --- |
| `dt` | yes | date | snapshot date, usually month-end |
| `vntg_mth` | yes | date | vintage month |
| `loan_age_m` | yes | string/number | loan age bucket; raw values can be `0..N`, and tail values can be written as `N+` or any numeric value greater than `N` when `max_loan_age_bucket` is configured |
| `mob` | yes | number | months on book |
| `risk_inst` | yes | number | disbursement amount; mainly used for `mob = 0` |
| `risk_outsd` | yes | number | outstanding balance for roll-rate and balance forecast |

## Optional dimension columns

You can add any number of optional business dimensions, for example:

- `channel`
- `project`
- `segment_l1`
- `segment_l2`
- `strategy_tag`
- any team-specific custom field

These columns are not fixed. If you want to use them for filtering or grouping, map them with:

- [dimension_config_example.json](dimension_config_example.json)

## Filling rules

- Keep the file in long-table format. Do not pivot before upload.
- Use numeric values for `mob`.
- If the source uses a tail bucket such as `4+` or `7+`, you can keep the `+` sign in `loan_age_m`.
- Keep `loan_age_m / mob` logically aligned. Minimum valid `mob` by bucket is:
  `0 -> 0`, `1 -> 1`, ..., `N -> N`, `N+ -> N+1`.
  Rows such as `loan_age_m=1, mob=0` or `loan_age_m=4+, mob=3` will be dropped by the skill during preprocessing.
- Keep `risk_inst` as `0` for non-`mob=0` rows unless your source table explicitly stores another value.
- Keep `risk_outsd` non-negative.
- Use the same date format across the file, for example `YYYY-MM-DD`.
- `dt` can be a month-end snapshot or an intra-month snapshot. The skill uses the latest available observed snapshot directly and does not apply day-count extrapolation.
- If one vintage/month combination has no balance for a bucket, either omit the row or fill `0`. Do not mix missing and string placeholders like `-` or `N/A`.

## Minimum usable dataset

At minimum, the table must let the model reconstruct:

- `MOB0_risk_inst`
- `risk_oustd0 ... risk_oustdN`
- `risk_oustdNplus`

So the file needs enough rows to cover:

- multiple `vntg_mth`
- `mob` values up to at least the forecast horizon you want, usually `19` or `20`
- loan age buckets `0..N`, plus tail-bucket rows for `N+`

## Suggested delivery package

For other users of the skill, the cleanest handoff is:

1. one CSV based on [input_table_template.csv](input_table_template.csv)
2. one dimension config JSON based on [dimension_config_example.json](dimension_config_example.json)
3. a short note with:
   - `start_dt`
   - `end_dt`
   - `product_term`
     for example `12`, `18`, `24`
     this controls the average-balance formula:
     denominator = `product_term + 0.5`
     numerator accumulates through `(MOB(product_term-1) + MOB(product_term)) / 2`
   - `mob_max`
      default is `20`; recommended rule is:
      `mob_max = 产品期数 + 最大逾期账龄`
      common consumer-loan default is `12 + 8 = 20`
   - `max_loan_age_bucket`
     default is `7`; this means `0..7 + 7+`
     if the source table is `0..4 + 4+`, set it to `4`
   - `bad_debt_start_age`
     default is `4`
     bad debt is calculated as `loan_age_m >= bad_debt_start_age` plus the tail bucket at `mob_max - 1`
   - roll-rate mode: weighted average or arithmetic average
   - tail recovery ratio
   - whether to use normal tail-bucket logic or high-period fallback
   - any manual roll-rate overrides

## Suggested prompt to ask the user

Before running the model, you can ask:

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
- `loan_age_m = 1` 的尾部 `1.01` 规则也会随产品期数变化：
  - `12`期产品：从 `mob14` 开始
  - `18`期产品：从 `mob20` 开始
  - 一般化后起点为 `产品期数 + 2`
- 全生命周期坏账、年化风险、放款不良率的观察终点也会随 `mob_max` 同步变化，默认使用 `mob_max - 1` 对应时点的 `M3+`。
  - `mob_max = 20` 时，看 `MOB19`
  - `mob_max = 14` 时，看 `MOB13`
- 如果账龄结构是 `0..4 + 4+`，请同步设置：
  - `max_loan_age_bucket = 4`
  - 若坏账定义仍为 `M3+`，则 `bad_debt_start_age = 4`
- 这时坏账统计口径会变成 `4 + 4+`。
- 如果 `mob_max` 较大，你需要提供更多历史样本；至少要保证样本覆盖的放款月数不小于 `mob_max`，否则高 MOB 区间的滚动率和余额预测会不稳定。
