#!/usr/bin/env python3

import argparse
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta


DECIMAL_FORMAT = "%.8f"


def parse_loan_age_bucket(value: object, max_loan_age_bucket: int) -> int:
    text = str(value).strip()
    if not text:
        return 0
    has_plus = text.endswith("+")
    if has_plus:
        text = text[:-1]
    number = pd.to_numeric(text, errors="coerce")
    if pd.isna(number):
        return 0
    bucket = max(0, int(round(float(number))))
    if has_plus or bucket > max_loan_age_bucket:
        return max_loan_age_bucket + 1
    return bucket


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the month-end roll-rate notebook logic on a flat CSV."
    )
    parser.add_argument("input_csv", help="Input CSV path")
    parser.add_argument("--group-field", help="Optional group field for segmented output")
    parser.add_argument("--output-overall", help="Output CSV path for overall results")
    parser.add_argument("--output-group", help="Output CSV path for grouped results")
    parser.add_argument(
        "--mob-max",
        type=int,
        default=20,
        help="Maximum MOB horizon to keep. Default 20, typically product term + max delinquency age, for example 12 + 8.",
    )
    parser.add_argument(
        "--product-term",
        type=int,
        default=12,
        help="Product term count used in average-balance aggregation. Default 12. For example, 18-term products should use 18.",
    )
    parser.add_argument(
        "--max-loan-age-bucket",
        type=int,
        default=7,
        help="Maximum finite loan-age bucket. Default 7, which means the model uses 0..7 plus 7+.",
    )
    parser.add_argument(
        "--bad-debt-start-age",
        type=int,
        default=4,
        help="First loan-age bucket included in bad-debt metrics. Default 4, which matches the current M3+ definition.",
    )
    parser.add_argument(
        "--recovery-rate-7plus",
        type=float,
        default=1.0,
        help="7+ recovery ratio, default 1.0",
    )
    return parser.parse_args()


def parse_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(",", "", regex=False), errors="coerce").fillna(0.0)


def clean_invalid_loan_age_mob_rows(raw: pd.DataFrame, max_loan_age_bucket: int) -> tuple[pd.DataFrame, dict[str, int]]:
    tail_bucket = max_loan_age_bucket + 1
    loan_age_capped = raw["loan_age_m"].clip(lower=0, upper=tail_bucket)
    min_valid_mob = loan_age_capped
    invalid_mask = raw["mob"] < min_valid_mob
    removed_rows = int(invalid_mask.sum())
    removed_risk_outsd = float(raw.loc[invalid_mask, "risk_outsd"].sum()) if removed_rows else 0.0
    removed_risk_inst = float(raw.loc[invalid_mask, "risk_inst"].sum()) if removed_rows else 0.0
    cleaned = raw.loc[~invalid_mask].copy()
    return cleaned, {
        "removed_rows": removed_rows,
        "removed_risk_outsd": removed_risk_outsd,
        "removed_risk_inst": removed_risk_inst,
    }


def load_input(path: str, group_field: str | None, mob_max: int, max_loan_age_bucket: int) -> pd.DataFrame:
    raw = pd.read_csv(path, dtype=str)
    raw.columns = [str(col).strip() for col in raw.columns]
    required = {"vntg_mth", "loan_age_m", "mob", "risk_inst", "risk_outsd"}
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    if group_field and group_field not in raw.columns:
        raise ValueError(f"Missing group field: {group_field}")

    raw["vntg_mth"] = pd.to_datetime(raw["vntg_mth"])
    raw["loan_age_m"] = raw["loan_age_m"].map(lambda value: parse_loan_age_bucket(value, max_loan_age_bucket))
    raw["mob"] = pd.to_numeric(raw["mob"], errors="coerce").round().astype(int)
    raw["risk_inst"] = parse_numeric(raw["risk_inst"])
    raw["risk_outsd"] = parse_numeric(raw["risk_outsd"])
    if "dt" in raw.columns:
        raw["dt"] = pd.to_datetime(raw["dt"])
    raw = raw[raw["mob"] <= mob_max].copy()
    raw, cleanup = clean_invalid_loan_age_mob_rows(raw, max_loan_age_bucket)
    raw.attrs["cleanup_summary"] = cleanup
    return raw


def preprocess_input(
    raw: pd.DataFrame,
    group_field: str | None,
) -> pd.DataFrame:
    group_keys = [group_field] if group_field else []
    if "dt" in raw.columns:
        agg_keys = group_keys + ["dt", "vntg_mth", "loan_age_m", "mob"]
        data = (
            raw.groupby(agg_keys, as_index=False)[["risk_inst", "risk_outsd"]]
            .sum()
            .sort_values("dt")
            .groupby(group_keys + ["vntg_mth", "loan_age_m", "mob"], as_index=False)
            .tail(1)
            .copy()
        )
    else:
        agg_keys = group_keys + ["vntg_mth", "loan_age_m", "mob"]
        data = raw.groupby(agg_keys, as_index=False)[["risk_inst", "risk_outsd"]].sum().copy()

    return data


def compute_metrics(
    data: pd.DataFrame,
    mob_max: int,
    recovery_rate_7plus: float,
    product_term: int,
    max_loan_age_bucket: int,
    bad_debt_start_age: int,
) -> pd.DataFrame:
    if product_term <= 0:
        raise ValueError("product_term must be positive")
    if product_term > mob_max:
        raise ValueError("product_term cannot exceed mob_max")
    if mob_max <= 0:
        raise ValueError("mob_max must be positive")
    if max_loan_age_bucket < 1:
        raise ValueError("max_loan_age_bucket must be at least 1")
    if bad_debt_start_age < 0:
        raise ValueError("bad_debt_start_age must be non-negative")
    if max_loan_age_bucket < bad_debt_start_age:
        raise ValueError("max_loan_age_bucket must be greater than or equal to bad_debt_start_age")
    start_dt = data["vntg_mth"].min().strftime("%Y-%m-%d")
    end_dt = data["vntg_mth"].max().strftime("%Y-%m-%d")
    bad_debt_mob = mob_max - 1
    age1_tail_start_mob = product_term + 2
    tail_bucket = max_loan_age_bucket + 1
    finite_ages = list(range(0, max_loan_age_bucket + 1))
    full_index = pd.date_range(start=start_dt, end=end_dt, freq="MS")

    def full_frame(cols: range | list[int]) -> pd.DataFrame:
        return pd.DataFrame(index=full_index, columns=list(cols), dtype=float)

    def mob_limit(frame: pd.DataFrame) -> int:
        if frame.empty:
            return -1
        cols = [int(c) for c in frame.columns if pd.notna(c)]
        return max(cols) if cols else -1

    def pivot_oustd(mask: pd.Series) -> pd.DataFrame:
        subset = data.loc[mask]
        if subset.empty:
            return pd.DataFrame()
        return subset.pivot_table(index="vntg_mth", columns="mob", values="risk_outsd", aggfunc="sum").astype(float)

    def get_real_oustd(frame: pd.DataFrame) -> pd.DataFrame:
        out = full_frame(range(0, mob_max + 1))
        if not frame.empty:
            common_idx = frame.index.intersection(out.index)
            common_cols = [c for c in frame.columns if c in out.columns]
            out.loc[common_idx, common_cols] = frame.loc[common_idx, common_cols].values
        out.index.name = "vntg_mth"
        out.columns.name = "mob"
        return out

    def get_init_oustd(frame: pd.DataFrame) -> pd.DataFrame:
        out = full_frame(range(0, mob_max + 1))
        common_idx = frame.index.intersection(out.index)
        common_cols = [c for c in frame.columns if c in out.columns]
        out.loc[common_idx, common_cols] = frame.loc[common_idx, common_cols].values
        out.fillna(0.0, inplace=True)
        out.index.name = "vntg_mth"
        out.columns.name = "mob"
        return out

    def get_pos_rollrate(
        pos_risk_oustd: pd.DataFrame,
        previous_risk_oustd: pd.DataFrame,
        start_mob: int,
        minus_months: int,
    ) -> pd.DataFrame:
        end_adjusted = (datetime.strptime(end_dt, "%Y-%m-%d") - relativedelta(months=minus_months)).strftime("%Y-%m-%d")
        idx = pd.date_range(start=start_dt, end=end_adjusted, freq="MS")
        out = pd.DataFrame(index=idx, columns=range(start_mob, mob_max), dtype=float)
        max_mob = mob_limit(pos_risk_oustd)
        for mob in range(start_mob, max_mob):
            for pos_mth in pos_risk_oustd.index:
                current = pos_risk_oustd.loc[pos_mth, mob] if mob in pos_risk_oustd.columns else np.nan
                prev = previous_risk_oustd.loc[pos_mth, mob - 1] if mob - 1 in previous_risk_oustd.columns else np.nan
                if current == 0 or prev == 0 or pd.isna(current) or pd.isna(prev) or np.isinf(current) or np.isinf(prev):
                    out.loc[pos_mth, mob] = None
                else:
                    out.loc[pos_mth, mob] = current / prev
        return out

    def get_recent_values(values: list[float], window: int) -> list[float]:
        clean = [x for x in values if x is not None and not pd.isna(x) and x != float("inf")]
        if not clean:
            return []
        return clean[-window:]

    def get_simple_mean(values: list[float], window: int) -> float:
        recent = get_recent_values(values, window)
        if not recent:
            return np.nan
        return float(np.mean(recent))

    def get_filtered_mean(values: list[float], window: int, clip_above_four: bool = False) -> float:
        recent = get_recent_values(values, window)
        if not recent:
            return np.nan
        mean_pos = np.mean(recent)
        std_pos = np.std(recent, ddof=1) if len(recent) > 1 else 0.0
        upper = mean_pos + 1.0 * std_pos
        lower = mean_pos - 1.0 * std_pos
        filtered = [value for value in recent if lower <= value <= upper]
        if clip_above_four:
            filtered = [value for value in filtered if value <= 4]
        mean_answer = np.mean(filtered)
        if np.isnan(mean_answer):
            mean_answer = mean_pos
        return float(mean_answer)

    def get_jiaquan_avg(pos_risk_oustd: pd.DataFrame, pos_rollrate: pd.DataFrame, start_mob: int, window: int = 6) -> pd.DataFrame:
        out = pd.DataFrame(index=["加权平均"], columns=range(0, mob_max + 1), dtype=float)
        n = len(pos_risk_oustd)
        for mob in range(start_mob, mob_max):
            total_weighted = 0.0
            total_balance = 0.0
            for j in range(n - mob - window, n - mob):
                if j < 0 or j >= n:
                    continue
                pos_mth = pos_risk_oustd.index[j]
                if pos_mth not in pos_rollrate.index or mob not in pos_rollrate.columns or mob not in pos_risk_oustd.columns:
                    continue
                roll = pos_rollrate.loc[pos_mth, mob]
                balance = pos_risk_oustd.loc[pos_mth, mob]
                if pd.isna(roll) or pd.isna(balance):
                    continue
                total_weighted += float(balance) * float(roll)
                total_balance += float(balance)
            if total_balance == 0:
                out.loc["加权平均", mob] = None
            else:
                avg = total_weighted / total_balance
                out.loc["加权平均", mob] = avg
        return out

    def get_simple_mean_frame(rollrate: pd.DataFrame, start_mob: int, window: int) -> pd.DataFrame:
        out = pd.DataFrame(index=[f"近{window}个月均值"], columns=range(0, mob_max + 1), dtype=float)
        for mob in range(start_mob, mob_max):
            series = rollrate[mob].tolist() if mob in rollrate.columns else []
            out.loc[f"近{window}个月均值", mob] = get_simple_mean(series, window)
        return out

    def get_filtered_mean_frame(rollrate: pd.DataFrame, start_mob: int, window: int, clip_above_four: bool = False) -> pd.DataFrame:
        out = pd.DataFrame(index=[f"近{window}个月算术平均"], columns=range(0, mob_max + 1), dtype=float)
        for mob in range(start_mob, mob_max):
            series = rollrate[mob].tolist() if mob in rollrate.columns else []
            out.loc[f"近{window}个月算术平均", mob] = get_filtered_mean(series, window, clip_above_four=clip_above_four)
        return out

    def get_final_rollrate(
        rollrate: pd.DataFrame,
        start_mob: int,
        final_rate: pd.DataFrame,
        len_months: int,
    ) -> pd.DataFrame:
        out = pd.DataFrame(index=full_index, columns=range(1, mob_max + 1), dtype=float)
        if not rollrate.empty:
            common_idx = rollrate.index.intersection(out.index)
            common_cols = [c for c in rollrate.columns if c in out.columns]
            out.loc[common_idx, common_cols] = rollrate.loc[common_idx, common_cols].values
        max_mob = mob_limit(rollrate)
        for mob in range(start_mob, max_mob + 1):
            for j in range(len_months - mob - 1, len_months):
                if j < 0 or j >= len(out.index):
                    continue
                pos_mth = out.index[j]
                if pd.isna(out.loc[pos_mth, mob]):
                    out.loc[pos_mth, mob] = final_rate.loc["最后使用", mob]
        return out

    def get_final_oustd(
        pos_risk_oustd_predict: pd.DataFrame,
        pre_risk_oustd_predict: pd.DataFrame,
        pos_rollrate_predict: pd.DataFrame,
        start_mob: int,
    ) -> pd.DataFrame:
        n = len(pos_risk_oustd_predict)
        for mob in range(start_mob, mob_max):
            for j in range(n - mob - 2, n):
                if j < 0 or j >= n:
                    continue
                pos_mth = pos_risk_oustd_predict.index[j]
                if pd.isna(pos_risk_oustd_predict.loc[pos_mth, mob]):
                    pos_risk_oustd_predict.loc[pos_mth, mob] = (
                        pre_risk_oustd_predict.loc[pos_mth, mob - 1] * pos_rollrate_predict.loc[pos_mth, mob]
                    )
        return pos_risk_oustd_predict

    mob0_pivot = (
        data[data["mob"] == 0]
        .pivot_table(index="vntg_mth", columns="mob", values="risk_inst", aggfunc="sum")
        .astype(float)
    )
    mob0_risk_inst = full_frame([0])
    if not mob0_pivot.empty and 0 in mob0_pivot.columns:
        common_idx = mob0_pivot.index.intersection(mob0_risk_inst.index)
        mob0_risk_inst.loc[common_idx, 0] = mob0_pivot.loc[common_idx, 0].values

    risk_oustd_real = {age: get_real_oustd(pivot_oustd(data["loan_age_m"] == age)) for age in finite_ages}
    risk_oustd_tail_real = get_real_oustd(pivot_oustd(data["loan_age_m"] == tail_bucket))

    rollrate0 = pd.DataFrame(index=full_index, columns=range(0, mob_max), dtype=float)
    rollrate0.iloc[:, 0] = risk_oustd_real[0].loc[:, 0] / mob0_risk_inst[0].values
    for mob in range(1, mob_max):
        for pos_mth in risk_oustd_real[0].index:
            current = risk_oustd_real[0].loc[pos_mth, mob]
            previous = risk_oustd_real[0].loc[pos_mth, mob - 1]
            if pd.isna(current) or pd.isna(previous):
                break
            if previous == 0:
                rollrate0.loc[pos_mth, mob] = None
            else:
                rollrate0.loc[pos_mth, mob] = current / previous

    rollrate1_end = (datetime.strptime(end_dt, "%Y-%m-%d") - relativedelta(months=1)).strftime("%Y-%m-%d")
    rollrate1 = pd.DataFrame(index=pd.date_range(start=start_dt, end=rollrate1_end, freq="MS"), columns=range(1, mob_max), dtype=float)
    max_mob1 = mob_limit(risk_oustd_real[1])
    for mob in range(1, max_mob1):
        if mob not in risk_oustd_real[1].columns:
            break
        for pos_mth in rollrate1.index:
            current = risk_oustd_real[1].loc[pos_mth, mob]
            previous = risk_oustd_real[0].loc[pos_mth, mob - 1]
            if pd.isna(current) or pd.isna(previous):
                break
            if previous == 0:
                rollrate1.loc[pos_mth, mob] = None
            else:
                rollrate1.loc[pos_mth, mob] = current / previous

    rollrate1_bei = pd.DataFrame(index=full_index, columns=range(2, mob_max), dtype=float)
    for mob in range(2, mob_max):
        if mob in rollrate1.columns and mob - 1 in rollrate1.columns:
            rollrate1_bei[mob] = rollrate1[mob] / rollrate1[mob - 1]

    rollrates = {age: get_pos_rollrate(risk_oustd_real[age], risk_oustd_real[age - 1], age, age) for age in range(2, max_loan_age_bucket + 1)}

    def build_final_rate(frame: pd.DataFrame, rollrate: pd.DataFrame, start_mob: int, clip_above_four: bool = False) -> pd.DataFrame:
        candidate_rows = [
            "近3个月均值",
            "近3个月算术平均",
            "近6个月均值",
            "近6个月算术平均",
            "近6个月加权平均",
            "最后使用",
        ]
        out = pd.DataFrame(index=candidate_rows, columns=range(0, mob_max + 1), dtype=float)
        out.loc["近3个月均值"] = get_simple_mean_frame(rollrate, start_mob, 3).loc["近3个月均值"]
        out.loc["近3个月算术平均"] = get_filtered_mean_frame(rollrate, start_mob, 3, clip_above_four=clip_above_four).loc["近3个月算术平均"]
        out.loc["近6个月均值"] = get_simple_mean_frame(rollrate, start_mob, 6).loc["近6个月均值"]
        out.loc["近6个月算术平均"] = get_filtered_mean_frame(rollrate, start_mob, 6, clip_above_four=clip_above_four).loc["近6个月算术平均"]
        out.loc["近6个月加权平均"] = get_jiaquan_avg(frame, rollrate, start_mob, window=6).loc["加权平均"]
        out.loc["最后使用"] = out.loc[candidate_rows[:-1]].max(axis=0)
        out.loc["最后使用"] = out.loc["最后使用"].clip(upper=0.9999)
        return out

    final_rate0 = build_final_rate(risk_oustd_real[0], rollrate0, 0)
    final_rate1 = build_final_rate(risk_oustd_real[1], rollrate1, 1)
    final_rates = {age: build_final_rate(risk_oustd_real[age], rollrates[age], age) for age in range(2, max_loan_age_bucket + 1)}

    final_rate1bei = pd.DataFrame(
        index=[
            "近3个月均值",
            "近3个月算术平均",
            "近6个月均值",
            "近6个月算术平均",
            "近6个月加权平均",
            "最后使用",
        ],
        columns=range(0, mob_max + 1),
        dtype=float,
    )
    final_rate1bei.loc["近3个月均值"] = get_simple_mean_frame(rollrate1_bei, 2, 3).loc["近3个月均值"]
    final_rate1bei.loc["近3个月算术平均"] = get_filtered_mean_frame(rollrate1_bei, 2, 3, clip_above_four=True).loc[
        "近3个月算术平均"
    ]
    final_rate1bei.loc["近6个月均值"] = get_simple_mean_frame(rollrate1_bei, 2, 6).loc["近6个月均值"]
    final_rate1bei.loc["近6个月算术平均"] = get_filtered_mean_frame(rollrate1_bei, 2, 6, clip_above_four=True).loc[
        "近6个月算术平均"
    ]
    final_rate1bei.loc["近6个月加权平均"] = get_jiaquan_avg(risk_oustd_real[1], rollrate1_bei, 2, window=6).loc["加权平均"]
    final_rate1bei.loc["最后使用"] = final_rate1bei.loc[
        ["近3个月均值", "近3个月算术平均", "近6个月均值", "近6个月算术平均", "近6个月加权平均"]
    ].max(axis=0)
    for mob in range(age1_tail_start_mob, mob_max + 1):
        final_rate1bei.loc["最后使用", mob] = 1.01

    len_months = len(rollrate0)
    rollrate0_predict = pd.DataFrame(index=full_index, columns=range(0, mob_max + 1), dtype=float)
    common_idx = rollrate0.index.intersection(rollrate0_predict.index)
    common_cols = [c for c in rollrate0.columns if c in rollrate0_predict.columns]
    rollrate0_predict.loc[common_idx, common_cols] = rollrate0.loc[common_idx, common_cols].values
    max_rollrate0 = mob_limit(rollrate0)
    for mob in range(0, max_rollrate0 + 1):
        for j in range(len_months - mob - 1, len_months):
            if j < 0 or j >= len(rollrate0_predict.index):
                continue
            pos_mth = rollrate0_predict.index[j]
            if pd.isna(rollrate0_predict.loc[pos_mth, mob]):
                rollrate0_predict.loc[pos_mth, mob] = final_rate0.loc["最后使用", mob]

    rollrate1_predict = pd.DataFrame(index=full_index, columns=range(1, mob_max + 1), dtype=float)
    common_idx = rollrate1.index.intersection(rollrate1_predict.index)
    common_cols = [c for c in rollrate1.columns if c in rollrate1_predict.columns]
    rollrate1_predict.loc[common_idx, common_cols] = rollrate1.loc[common_idx, common_cols].values
    adjustment = pd.DataFrame(index=full_index, columns=[0], dtype=float)
    adjustment.iloc[:, :] = 1.0
    for j in range(len(rollrate1_predict.index) - 2, len(rollrate1_predict.index)):
        if j < 0:
            continue
        pos_mth = rollrate1_predict.index[j]
        if pd.isna(rollrate1_predict.loc[pos_mth, 1]):
            rollrate1_predict.loc[pos_mth, 1] = final_rate1.loc["最后使用", 1] * adjustment.loc[pos_mth, 0]
    for mob in range(2, min(age1_tail_start_mob, mob_max)):
        for j in range(len(rollrate1_predict.index) - mob - 1, len(rollrate1_predict.index)):
            if j < 0:
                continue
            pos_mth = rollrate1_predict.index[j]
            if pd.isna(rollrate1_predict.loc[pos_mth, mob]):
                rollrate1_predict.loc[pos_mth, mob] = (
                    rollrate1_predict.loc[pos_mth, mob - 1] * final_rate1bei.loc["最后使用", mob]
                )
                if j == len(rollrate1_predict.index) - mob - 1:
                    rollrate1_predict.loc[pos_mth, mob] = rollrate1_predict.loc[pos_mth, mob] * adjustment.loc[pos_mth, 0]
    for mob in range(max(age1_tail_start_mob, 2), mob_max):
        for pos_mth in rollrate1_predict.index:
            if pd.isna(rollrate1_predict.loc[pos_mth, mob]):
                candidate = rollrate1_predict.loc[pos_mth, mob - 1] * final_rate1bei.loc["最后使用", mob]
                rollrate1_predict.loc[pos_mth, mob] = 0.999 if candidate > 1 else candidate

    rollrate_predicts = {
        age: get_final_rollrate(rollrates[age], age, final_rates[age], len_months)
        for age in range(2, max_loan_age_bucket + 1)
    }

    risk_oustd_predict = {age: risk_oustd_real[age].copy() for age in finite_ages}
    risk_oustd_tail_predict = get_init_oustd(risk_oustd_tail_real)

    for j in range(len(risk_oustd_predict[0].index) - 2, len(risk_oustd_predict[0].index)):
        if j < 0:
            continue
        pos_mth = risk_oustd_predict[0].index[j]
        if pd.isna(risk_oustd_predict[0].loc[pos_mth, 0]):
            risk_oustd_predict[0].loc[pos_mth, 0] = mob0_risk_inst.loc[pos_mth, 0] * rollrate0_predict.loc[pos_mth, 0]
    for mob in range(1, mob_max):
        for j in range(len(risk_oustd_predict[0].index) - mob - 2, len(risk_oustd_predict[0].index)):
            if j < 0:
                continue
            pos_mth = risk_oustd_predict[0].index[j]
            if pd.isna(risk_oustd_predict[0].loc[pos_mth, mob]):
                risk_oustd_predict[0].loc[pos_mth, mob] = (
                    risk_oustd_predict[0].loc[pos_mth, mob - 1] * rollrate0_predict.loc[pos_mth, mob]
                )
    risk_oustd_predict[0].fillna(0.0, inplace=True)

    risk_oustd_predict[1] = get_final_oustd(risk_oustd_predict[1], risk_oustd_predict[0], rollrate1_predict, 1).fillna(0.0)
    for age in range(2, max_loan_age_bucket + 1):
        risk_oustd_predict[age] = get_final_oustd(
            risk_oustd_predict[age],
            risk_oustd_predict[age - 1],
            rollrate_predicts[age],
            age,
        ).fillna(0.0)

    if tail_bucket in risk_oustd_tail_predict.columns and max_loan_age_bucket in risk_oustd_predict[max_loan_age_bucket].columns:
        risk_oustd_tail_predict.loc[:, tail_bucket] = (
            risk_oustd_predict[max_loan_age_bucket].loc[:, max_loan_age_bucket] * recovery_rate_7plus
        )
    for mob in range(tail_bucket + 1, mob_max + 1):
        for pos_mth in risk_oustd_tail_predict.index:
            risk_oustd_tail_predict.loc[pos_mth, mob] = (
                risk_oustd_tail_predict.loc[pos_mth, mob - 1] + risk_oustd_predict[max_loan_age_bucket].loc[pos_mth, mob - 1]
            ) * recovery_rate_7plus
    risk_oustd_tail_predict.fillna(0.0, inplace=True)

    risk_oustd_tot = full_frame(range(0, mob_max + 1)).fillna(0.0)
    for mob in range(0, mob_max + 1):
        risk_oustd_tot[mob] = sum(risk_oustd_predict[age][mob] for age in finite_ages) + risk_oustd_tail_predict[mob]

    risk_oustd_mean = full_frame(range(0, mob_max + 1))
    for mob in range(1, mob_max + 1):
        risk_oustd_mean[mob] = (risk_oustd_tot[mob] + risk_oustd_tot[mob - 1]) / 2
    risk_oustd_mean[0] = (risk_oustd_tot[0] + mob0_risk_inst[0].values) / 4

    mean_oustd = pd.DataFrame(index=full_index, columns=[0], dtype=float)
    for idx in range(len(mean_oustd)):
        mean_oustd.iloc[idx] = risk_oustd_mean.iloc[idx, 0 : product_term + 1].sum() / (product_term + 0.5)

    duration = pd.DataFrame(index=full_index, columns=[0], dtype=float)
    duration[0] = product_term * mean_oustd[0] / mob0_risk_inst[0].values

    bad_oustd = pd.DataFrame(index=full_index, columns=[0], dtype=float)
    bad_debt_ages = [age for age in range(bad_debt_start_age, max_loan_age_bucket + 1)]
    for idx in range(len(bad_oustd)):
        bad_oustd.iloc[idx] = sum(risk_oustd_predict[age].iloc[idx, bad_debt_mob] for age in bad_debt_ages)
        if tail_bucket >= bad_debt_start_age:
            bad_oustd.iloc[idx] += risk_oustd_tail_predict.iloc[idx, bad_debt_mob]

    risk = pd.DataFrame(index=full_index, columns=[0], dtype=float)
    risk[0] = bad_oustd[0] / mean_oustd[0]
    bad_inst_ratio = pd.DataFrame(index=full_index, columns=[0], dtype=float)
    bad_inst_ratio[0] = bad_oustd[0] / mob0_risk_inst[0].values

    return (
        pd.concat(
            [
                mean_oustd.rename(columns={0: "平均余额"}),
                duration.rename(columns={0: "久期"}),
                risk.rename(columns={0: "年化风险"}),
                bad_oustd.rename(columns={0: "全生命周期坏账"}),
                bad_inst_ratio.rename(columns={0: "放款不良率"}),
                mob0_risk_inst.rename(columns={0: "MOB0放款额"}),
            ],
            axis=1,
        )
        .reset_index()
        .rename(columns={"index": "vntg_mth"})
    )


def main() -> None:
    args = parse_args()
    raw = load_input(args.input_csv, args.group_field, args.mob_max, args.max_loan_age_bucket)
    data = preprocess_input(raw, args.group_field)

    overall = compute_metrics(
        data[["vntg_mth", "loan_age_m", "mob", "risk_inst", "risk_outsd"]].copy(),
        mob_max=args.mob_max,
        recovery_rate_7plus=args.recovery_rate_7plus,
        product_term=args.product_term,
        max_loan_age_bucket=args.max_loan_age_bucket,
        bad_debt_start_age=args.bad_debt_start_age,
    )
    if args.output_overall:
        Path(args.output_overall).parent.mkdir(parents=True, exist_ok=True)
        overall.to_csv(args.output_overall, index=False, float_format=DECIMAL_FORMAT)
    print("OVERALL_LATEST")
    print(overall.tail(1).to_string(index=False, float_format=lambda x: DECIMAL_FORMAT % x))

    if args.group_field and args.output_group:
        results = []
        for group_value, group_df in data.groupby(args.group_field, dropna=False):
            result = compute_metrics(
                group_df[["vntg_mth", "loan_age_m", "mob", "risk_inst", "risk_outsd"]].copy(),
                mob_max=args.mob_max,
                recovery_rate_7plus=args.recovery_rate_7plus,
                product_term=args.product_term,
                max_loan_age_bucket=args.max_loan_age_bucket,
                bad_debt_start_age=args.bad_debt_start_age,
            )
            result.insert(0, args.group_field, group_value)
            results.append(result)
        grouped = pd.concat(results, ignore_index=True)
        Path(args.output_group).parent.mkdir(parents=True, exist_ok=True)
        grouped.to_csv(args.output_group, index=False, float_format=DECIMAL_FORMAT)
        print("GROUP_LATEST")
        print(grouped.groupby(args.group_field).tail(1).to_string(index=False, float_format=lambda x: DECIMAL_FORMAT % x))


if __name__ == "__main__":
    main()
