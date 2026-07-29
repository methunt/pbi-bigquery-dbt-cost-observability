"""Generate the synthetic sample dataset backing the CSV mode of this report.

No production data is used. Every value here is fabricated by this script from a
fixed seed, so a given SEED always produces byte-identical CSVs and anyone can
regenerate or audit the dataset without BigQuery access.

What is deliberately *not* random is the shape. A uniformly random dataset would
hide the findings the report exists to surface, so the generator reproduces the
structural facts that make BigQuery + dbt cost analysis awkward in the first
place:

  1. Script children bill.  A multi-statement SCRIPT parent reports the exact sum
     of its children's billed bytes, and both parent and child rows exist. Any
     cost measure must therefore restrict to top-level jobs (parent_job_id blank)
     or it counts the whole script twice.
  2. Billed >= processed.  On-demand billing applies a 10 MB per-job floor and
     rounds up to the next MB, so small jobs are billed for more than they scan.
     The difference is the "minimum-billing waste" the report reports on.
  3. Cache hits bill nothing.  total_bytes_billed is 0 when cache_hit is true.
  4. Node coverage is partial.  dbt's own run metadata only covers jobs it
     records; post-hook and run-operation jobs are separate submissions that
     never appear there and are reachable only through job labels. A meaningful
     share of spend therefore lands on run-level attribution rather than a node,
     which is why the report never groups spend by dbt layer.
  5. Rate varies by region.  europe-west3 is priced above eu, so identical bytes
     cost different amounts depending on where the job ran.

Usage:  python scripts/generate_sample_data.py [--out ../sample-data] [--seed 20260701]
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

SEED = 20260701

# One completed calendar month, exclusive end, matching RangeStart/RangeEnd
# semantics. It must sit wholly in the past: dim_Date is generated from
# #date(2026,1,1) to DateTime.LocalNow(), so any job dated after today has no
# matching date row and drops out of every date-driven visual. A finished month
# stays valid however long from now the project is opened.
WINDOW_START = date(2026, 6, 1)
WINDOW_END = date(2026, 7, 1)

PROJECT = "acme-analytics-prod"
DBT_PROJECT = "acme_warehouse"

REGIONS = ["eu", "europe-west3"]
REGION_WEIGHTS = [0.72, 0.28]

MB = 1024 ** 2
GB = 1024 ** 3
TIB = 1024 ** 4

BILLING_FLOOR_BYTES = 10 * MB  # BigQuery on-demand per-job minimum

# Failure rates. Low on purpose - the health and "nodes failed" pages are meant to
# read as a short list you could actually work through, so a handful of failures
# across the month tells the story better than a wall of red.
FAIL_RATE_NODE = 0.004
SKIP_RATE_NODE = 0.005
FAIL_RATE_TEST = 0.007
ERROR_RATE_TEST = 0.003
FAIL_RATE_ADHOC = 0.005

# Runs per day, by weekday: CI is busy Mon-Fri, quiet at the weekend.
RUNS_PER_WEEKDAY = [4, 4, 4, 4, 3, 1, 1]  # Mon..Sun

HUMAN_USERS = [
    "alex.morgan@example.com",
    "priya.raman@example.com",
    "dan.okafor@example.com",
    "mei.tanaka@example.com",
]
DBT_SERVICE_ACCOUNT = f"dbt-runner@{PROJECT}.iam.gserviceaccount.com"
BI_SERVICE_ACCOUNT = f"powerbi-refresh@{PROJECT}.iam.gserviceaccount.com"

# --------------------------------------------------------------------------- #
# dbt project shape
# --------------------------------------------------------------------------- #

# (model_name, folder, subfolder, materialization, upstream_count, scan_gb, tags)
#
# `folder` and `subfolder` are the model's location on disk, NOT a layer label.
# The model derives layer and sublayer from the path - segment 1 is the layer and
# segment 2 the sublayer - so the folder here is what ends up as the layer, and
# the two cannot disagree.
MODEL_SPECS = [
    # staging - views over raw, cheap
    ("stg_orders",            "staging",   "raw",          "view",      1,   0.0,  ["staging"]),
    ("stg_customers",         "staging",   "raw",          "view",      1,   0.0,  ["staging"]),
    ("stg_sessions",          "staging",   "raw",          "view",      1,   0.0,  ["staging"]),
    ("stg_impressions",       "staging",   "raw",          "view",      1,   0.0,  ["staging"]),
    ("stg_spend",             "staging",   "raw",          "view",      1,   0.0,  ["staging"]),
    ("stg_products",          "staging",   "raw",          "view",      1,   0.0,  ["staging"]),
    ("stg_campaigns",         "staging",   "raw",          "view",      1,   0.0,  ["staging"]),
    ("stg_exchange_rates",    "staging",   "raw",          "view",      1,   0.0,  ["staging"]),
    # warehouse - the expensive incremental facts
    ("int_sessions_enriched", "warehouse", "intermediate", "ephemeral", 2,   0.0,  ["warehouse"]),
    ("int_order_items",       "warehouse", "intermediate", "ephemeral", 2,   0.0,  ["warehouse"]),
    ("dim_customer",          "warehouse", "dimension",    "table",     2,   4.2,  ["warehouse"]),
    ("dim_product",           "warehouse", "dimension",    "table",     1,   0.8,  ["warehouse"]),
    ("dim_campaign",          "warehouse", "dimension",    "table",     1,   0.3,  ["warehouse"]),
    ("dim_date",              "warehouse", "dimension",    "table",     0,   0.01, ["warehouse"]),
    ("fct_orders",            "warehouse", "fact",         "incremental", 3, 38.0, ["warehouse", "critical"]),
    ("fct_sessions",          "warehouse", "fact",         "incremental", 3, 121.0, ["warehouse", "critical"]),
    ("fct_impressions",       "warehouse", "fact",         "incremental", 2, 264.0, ["warehouse", "critical"]),
    ("fct_spend",             "warehouse", "fact",         "incremental", 3, 17.5, ["warehouse"]),
    ("fct_attribution",       "warehouse", "fact",         "table",     4, 152.0,  ["warehouse", "critical"]),
    # marts
    ("mart_revenue_daily",    "marts",     "revenue",      "table",     3,  22.0,  ["marts"]),
    ("mart_channel_perf",     "marts",     "marketing",    "table",     4,  31.0,  ["marts"]),
    ("mart_customer_ltv",     "marts",     "revenue",      "table",     3,  48.0,  ["marts"]),
    ("mart_campaign_roi",     "marts",     "marketing",    "table",     4,  26.5,  ["marts"]),
    ("mart_funnel",           "marts",     "marketing",    "table",     3,  19.0,  ["marts"]),
    ("mart_cohorts",          "marts",     "revenue",      "table",     2,  35.0,  ["marts", "expensive"]),
    # snapshots live outside models/, so the path rule lands them on '(root)'.
    # That is a faithful quirk of the derivation, not a bug in the data.
    ("snap_customer_tier",    "snapshots", "",             "snapshot",  1,   3.1,  ["snapshot"]),
    ("snap_product_price",    "snapshots", "",             "snapshot",  1,   1.4,  ["snapshot"]),
]


def model_path(name: str, folder: str, subfolder: str) -> str:
    """Where the model's .sql sits. Snapshots live in snapshots/, everything else
    under models/<folder>/<subfolder>/."""
    if folder == "snapshots":
        return f"snapshots/{name}.sql"
    return f"models/{folder}/{subfolder}/{name}.sql"


def derive_layer_sublayer(path: str) -> tuple[str, str]:
    """Mirror the model's own path-based derivation exactly.

    Segment 0 is always the top folder, so the layer is segment 1 and the sublayer
    segment 2. A segment ending in .sql means the model sits directly in that
    folder, so there is no deeper level.
    """
    parts = path.replace("\\", "/").split("/")
    seg1 = parts[1] if len(parts) > 1 else ""
    seg2 = parts[2] if len(parts) > 2 else ""
    layer = "(root)" if seg1.endswith(".sql") else seg1
    sublayer = "" if seg2.endswith(".sql") or not seg2 else seg2
    return layer, sublayer

# Tests: (test_name, tested_model, scan_gb)
TEST_SPECS = [
    ("not_null_fct_orders_order_id",          "fct_orders",         2.1),
    ("unique_fct_orders_order_id",            "fct_orders",         3.4),
    ("relationships_fct_orders_customer_id",  "fct_orders",         5.2),
    ("not_null_fct_sessions_session_id",      "fct_sessions",       8.8),
    ("unique_fct_sessions_session_id",        "fct_sessions",      11.2),
    ("accepted_values_fct_sessions_channel",  "fct_sessions",       6.4),
    ("not_null_dim_customer_customer_id",     "dim_customer",       0.4),
    ("unique_dim_customer_customer_id",       "dim_customer",       0.6),
    ("not_null_fct_impressions_impression_id", "fct_impressions",  18.0),
    ("unique_mart_revenue_daily_date_channel", "mart_revenue_daily", 1.2),
    ("not_null_fct_spend_spend_id",           "fct_spend",          1.9),
    ("relationships_fct_spend_campaign_id",   "fct_spend",          2.6),
    ("accepted_values_dim_product_category",  "dim_product",        0.3),
    ("not_null_fct_attribution_touch_id",     "fct_attribution",    9.4),
]

# Post-hook / run-operation jobs. These are the ones dbt's own metadata never
# records - separate submissions, reachable only via job labels. Fact 4 above.
HOOK_SPECS = [
    ("repair_fct_sessions_partitions", "fct_sessions", 178.0),
    ("purge_expired_sessions",         "fct_sessions",  42.0),
    ("repair_fct_orders_partitions",   "fct_orders",    31.0),
    ("grant_select_analytics",         None,             0.0),
    ("stage_external_sources",         None,             0.6),
]

ERROR_REASONS = [
    ("notFound", "Not found: Table {t} was not found in location EU"),
    ("quotaExceeded", "Quota exceeded: Your project exceeded quota for concurrent queries"),
    ("invalidQuery", "Syntax error: Unexpected end of script at [1:2048]"),
    ("resourcesExceeded", "Resources exceeded during query execution: query used too much memory"),
    ("accessDenied", "Access Denied: Table {t}: User does not have permission to query table"),
]

BIE_DECLINE = [
    ("INPUT_TOO_LARGE", "The input to the query exceeds the BI Engine capacity"),
    ("UNSUPPORTED_SQL_TEXT", "The query contains SQL constructs BI Engine does not support"),
    ("OTHER_REASON", "BI Engine was unable to accelerate this query"),
]

# Ad-hoc / BI query patterns, so the non-dbt side of the fact table is not
# uniform noise. (label_app, statement_type, scan_gb_range, is_service)
ADHOC_PATTERNS = [
    ("powerbi",  "SELECT", (0.4, 48.0), True),
    ("looker",   "SELECT", (0.2, 22.0), True),
    ("notebook", "SELECT", (0.1, 96.0), False),
    ("console",  "SELECT", (0.01, 12.0), False),
    ("console",  "CREATE_TABLE_AS_SELECT", (2.0, 60.0), False),
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def billed_bytes(processed: int, cache_hit: bool) -> int:
    """Apply BigQuery on-demand billing rules: cache is free, 10 MB floor, round
    up to the next whole MB. This is why billed can exceed processed."""
    if cache_hit:
        return 0
    if processed <= BILLING_FLOOR_BYTES:
        return BILLING_FLOOR_BYTES
    return -(-processed // MB) * MB  # ceil to MB


def slot_hr(slot_ms: int) -> str:
    """Format slot time as HH:MM:SS, matching the TIME value the BigQuery path
    produces via TIME(TIMESTAMP_MICROS(...))."""
    total = int(slot_ms / 1000)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def size_category(processed: int) -> int:
    """Band bytes processed into the four CategoryID values the fact SQL assigns,
    so the CSV path and the BigQuery path agree on the size dimension."""
    if processed < MB:
        return 1
    if processed < 100 * MB:
        return 2
    if processed < GB:
        return 3
    return 4


def runtime_for(processed: int, rng: random.Random) -> float:
    """Duration correlates with bytes scanned but only loosely - the report makes
    the point that slow and expensive are different problems."""
    base = 0.6 + (processed / GB) * rng.uniform(0.04, 0.22)
    return round(base * rng.uniform(0.5, 2.4), 3)


def hash_hex(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


def make_uuid(rng: random.Random) -> str:
    h = hash_hex(rng, 32)
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def gb_jitter(base_gb: float, rng: random.Random, full_refresh: bool) -> int:
    """Bytes for one execution of a model. Incremental runs scan a slice; a full
    refresh scans everything, which is where the cost spikes come from."""
    if base_gb == 0:
        return 0
    if full_refresh:
        return int(base_gb * GB * rng.uniform(3.0, 5.5))
    return int(base_gb * GB * rng.uniform(0.82, 1.24))


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #

class Generator:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.jobs: list[dict] = []
        self.invocations: list[dict] = []
        self.node_executions: list[dict] = []
        self.job_statements: list[dict] = []
        self.job_counter = 0

    # -- identifiers -------------------------------------------------------- #

    def new_job_id(self, when: datetime) -> str:
        self.job_counter += 1
        return (
            f"{PROJECT}:EU.job_{when.strftime('%Y%m%d%H%M%S')}"
            f"_{self.job_counter:06d}_{hash_hex(self.rng, 6)}"
        )

    # -- fact rows ---------------------------------------------------------- #

    def add_job(
        self,
        *,
        when: datetime,
        user_email: str,
        statement_type: str,
        processed: int,
        region: str,
        cache_hit: bool = False,
        parent_job_id: str = "",
        destination_table_id: str = "",
        query: str = "",
        label_app: str = "",
        label_project: str = "",
        label_env: str = "",
        label_model_name: str = "",
        label_resource_type: str = "",
        label_invocation_id: str = "",
        is_dbt_job: bool = False,
        failed: bool = False,
        bie_mode: str = "DISABLED",
        bie_error: tuple[str, str] | None = None,
        duration_override: float | None = None,
        billed_override: int | None = None,
    ) -> str:
        job_id = self.new_job_id(when)
        duration = duration_override if duration_override is not None else runtime_for(processed, self.rng)
        end = when + timedelta(seconds=duration)
        queue = timedelta(milliseconds=self.rng.randint(20, 900))
        start = when + queue

        if failed:
            billed = 0
            processed_final = 0
            reason, msg_tpl = self.rng.choice(ERROR_REASONS)
            err_msg = msg_tpl.format(t=destination_table_id or "unknown_table")
            err_loc = "query" if reason in ("invalidQuery", "resourcesExceeded") else (destination_table_id or "")
            statement_type_final = "FAILED"
        else:
            processed_final = processed
            billed = billed_override if billed_override is not None else billed_bytes(processed, cache_hit)
            reason = err_loc = err_msg = ""
            statement_type_final = statement_type

        slot_ms = 0 if cache_hit or failed else int(duration * 1000 * self.rng.uniform(1.5, 26.0))

        self.jobs.append({
            "job_id": job_id,
            "creation_date": when.date().isoformat(),
            "creation_time": when.strftime("%H:%M:%S"),
            "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": end.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_sec": int(round(duration)),
            "cache_hit": "TRUE" if cache_hit else "FALSE",
            "creation_hour_24": when.hour,
            "total_slot_hr": slot_hr(slot_ms),
            "DataSizeCategoryID": size_category(processed_final),
            "total_bytes_processed": processed_final,
            "total_bytes_billed": billed,
            "error_reason": reason,
            "error_location": err_loc,
            "error_message": err_msg,
            "user_email": user_email,
            "statement_type": statement_type_final,
            "queryhash": hash_hex(self.rng, 16) if not query else f"{abs(hash(query)) & 0xFFFFFFFFFFFFFFFF:016x}",
            "region": region,
            "bie_mode": bie_mode,
            "label_app": label_app,
            "label_project": label_project,
            "bie_error_code": bie_error[0] if bie_error else "",
            "bie_error_reason": bie_error[1] if bie_error else "",
            "label_env": label_env,
            "parent_job_id": parent_job_id,
            "label_model_name": label_model_name,
            "label_resource_type": label_resource_type,
            "label_invocation_id": label_invocation_id,
            "total_slot_ms": slot_ms,
            "destination_table_id": destination_table_id,
            "query": query,
            "is_dbt_job": "TRUE" if is_dbt_job else "FALSE",
        })
        return job_id

    # -- dbt runs ----------------------------------------------------------- #

    def emit_invocation(self, run_start: datetime, env: str, command: str, full_refresh: bool) -> None:
        inv_id = make_uuid(self.rng)
        threads = 8
        target_schema = "analytics" if env == "prod" else f"dbt_{env}"

        self.invocations.append({
            "command_invocation_id": inv_id,
            "dbt_command": command,
            "dbt_vars": "{}" if not full_refresh else '{"full_refresh_scope":"facts"}',
            "dbt_version": "1.8.7",
            "env_vars": f'{{"DBT_ENV":"{env}"}}',
            "full_refresh_flag": "TRUE" if full_refresh else "FALSE",
            "project_name": DBT_PROJECT,
            "run_date": run_start.date().isoformat(),
            "run_started_at": run_start.strftime("%Y-%m-%d %H:%M:%S"),
            "target_name": env,
            "target_schema": target_schema,
            "target_threads": threads,
        })

        # Which nodes this run touches. A full refresh or a nightly prod build
        # covers everything; CI runs touch a subset.
        if command == "test":
            selected_models = []
            selected_tests = TEST_SPECS
        elif full_refresh or env == "prod":
            selected_models = MODEL_SPECS
            selected_tests = TEST_SPECS
        else:
            k = self.rng.randint(8, len(MODEL_SPECS))
            selected_models = self.rng.sample(MODEL_SPECS, k)
            selected_tests = self.rng.sample(TEST_SPECS, self.rng.randint(3, len(TEST_SPECS)))

        cursor = run_start
        thread_cycle = 0

        for (name, layer, sublayer, matz, upstream, scan_gb, tags) in selected_models:
            thread_cycle += 1
            node_started = cursor + timedelta(seconds=self.rng.uniform(0.2, 3.0))
            processed = gb_jitter(scan_gb, self.rng, full_refresh)

            # Ephemeral models are compiled into their parents - no BigQuery job.
            has_job = matz not in ("ephemeral",)
            # A few nodes fail or are skipped so the health visuals have something to
            # show, but kept deliberately low: the failure pages should read as a
            # short, diagnosable list, not a wall of red.
            roll = self.rng.random()
            if roll < FAIL_RATE_NODE:
                status = "error"
            elif roll < FAIL_RATE_NODE + SKIP_RATE_NODE:
                status = "skipped"
            else:
                status = "success"

            duration = 0.0 if not has_job else runtime_for(processed, self.rng)
            node_end = node_started + timedelta(seconds=max(duration, 0.4))

            job_id = ""
            if has_job and status != "skipped":
                # Incremental models run as a multi-statement SCRIPT: a temp table
                # is built, then merged. That parent/child pair is fact 1.
                if matz == "incremental" and not full_refresh:
                    job_id = self.emit_script_job(
                        when=node_started, inv_id=inv_id, env=env, model=name,
                        processed=processed, failed=(status == "error"),
                        resource_type="model",
                    )
                else:
                    stmt = {
                        "view": "CREATE_VIEW",
                        "table": "CREATE_TABLE_AS_SELECT",
                        "incremental": "CREATE_TABLE_AS_SELECT",
                        "snapshot": "MERGE",
                    }[matz]
                    job_id = self.add_job(
                        when=node_started,
                        user_email=DBT_SERVICE_ACCOUNT,
                        statement_type=stmt,
                        processed=processed,
                        region=self.pick_region(),
                        destination_table_id=name,
                        query=self.model_sql(name, matz, upstream),
                        label_app="dbt",
                        label_project=DBT_PROJECT,
                        label_env=env,
                        label_model_name=name,
                        label_resource_type="snapshot" if matz == "snapshot" else "model",
                        label_invocation_id=inv_id,
                        is_dbt_job=True,
                        failed=(status == "error"),
                        duration_override=duration,
                    )

            exec_id = make_uuid(self.rng)
            self.node_executions.append({
                "node_execution_id": exec_id,
                "command_invocation_id": inv_id,
                "node_id": node_id_for(name, matz),
                "node_name": name,
                # 'Model' covers snapshots too - both come from model_executions.
                "node_type": "Model",
                "alias": name,
                "schema_name": target_schema,
                "materialization": matz,
                "status": status,
                # Skipped and ephemeral nodes never reach BigQuery, so they have no
                # job id. A blank would be a duplicate on the one side of the
                # relationship to Fact, so a per-execution sentinel stands in - the
                # same COALESCE the BigQuery query applies.
                "job_id": job_id or f"no-job:model:{exec_id}",
                "adapter_code": "" if status == "skipped" else ("ERROR" if status == "error" else "OK"),
                "message": "" if status == "success" else (
                    "Skipped because an upstream node failed" if status == "skipped"
                    else "Database Error in model {} - see error_message on the job".format(name)
                ),
                "rows_affected": "" if status != "success" or not has_job else self.rng.randint(120, 4_200_000),
                "bytes_processed": processed if status == "success" else 0,
                "slot_ms": int(duration * 1000 * self.rng.uniform(1.5, 26.0)) if has_job else 0,
                "total_node_runtime": round((node_end - node_started).total_seconds(), 3),
                "compile_started_at": node_started.strftime("%Y-%m-%d %H:%M:%S"),
                "query_completed_at": node_end.strftime("%Y-%m-%d %H:%M:%S"),
                "run_started_at": run_start.strftime("%Y-%m-%d %H:%M:%S"),
                "run_date": run_start.date().isoformat(),
                "thread_id": f"Thread-{(thread_cycle % 8) + 1}",
                "was_full_refresh": "TRUE" if full_refresh else "FALSE",
                # tags, failures and the test_* columns belong to test rows only;
                # the model branch of the union selects them as NULL.
                "tags": "",
                "test_node_id": "",
                "test_path": "",
                "tested_nodes": "",
            })

            # Post-hooks fire as independent submissions after their model. dbt
            # never records them, so they carry labels but no node execution.
            if status == "success" and name in ("fct_sessions", "fct_orders"):
                for (hook_name, hook_target, hook_gb) in HOOK_SPECS:
                    if hook_target != name:
                        continue
                    if self.rng.random() > 0.85:
                        continue
                    self.add_job(
                        when=node_end + timedelta(seconds=self.rng.uniform(0.5, 4.0)),
                        user_email=DBT_SERVICE_ACCOUNT,
                        statement_type="MERGE" if "repair" in hook_name else "DELETE",
                        processed=gb_jitter(hook_gb, self.rng, False),
                        region=self.pick_region(),
                        destination_table_id=name,
                        query=f"-- post-hook: {hook_name}\nMERGE `{PROJECT}.analytics.{name}` ...",
                        label_app="dbt",
                        label_project=DBT_PROJECT,
                        label_env=env,
                        label_model_name=name,
                        # dbt never records these in its own run metadata, so the
                        # model-name label is the only route to attributing their
                        # spend to a node -> "Node (label)".
                        label_resource_type="model",
                        label_invocation_id=inv_id,
                        is_dbt_job=True,
                    )

            cursor = node_end

        # Tests
        for (test_name, tested_model, scan_gb) in selected_tests:
            thread_cycle += 1
            t_start = cursor + timedelta(seconds=self.rng.uniform(0.1, 1.5))
            processed = gb_jitter(scan_gb, self.rng, False)
            duration = runtime_for(processed, self.rng)
            t_end = t_start + timedelta(seconds=duration)
            failures = 0
            roll = self.rng.random()
            status = "pass"
            if roll < FAIL_RATE_TEST:
                status = "fail"
                failures = self.rng.randint(1, 340)
            elif roll < FAIL_RATE_TEST + ERROR_RATE_TEST:
                status = "error"

            job_id = self.add_job(
                when=t_start,
                user_email=DBT_SERVICE_ACCOUNT,
                statement_type="SELECT",
                processed=processed,
                region=self.pick_region(),
                destination_table_id="(anonymous SELECT result)",
                query=f"-- test: {test_name}\nSELECT count(*) FROM `{PROJECT}.analytics.{tested_model}` WHERE ...",
                label_app="dbt",
                label_project=DBT_PROJECT,
                label_env=env,
                # dbt lowercases and truncates label values at 63 chars.
                label_model_name=test_name.lower()[:63],
                label_resource_type="test",
                label_invocation_id=inv_id,
                is_dbt_job=True,
                failed=(status == "error"),
                duration_override=duration,
            )

            test_exec_id = make_uuid(self.rng)
            self.node_executions.append({
                "node_execution_id": test_exec_id,
                "command_invocation_id": inv_id,
                # A test row's node_id is the model it tests, not the test itself.
                # That is what lets a test inherit its layer from dbt_models; the
                # test's own identity lives in test_node_id.
                "node_id": f"model.{DBT_PROJECT}.{tested_model}",
                "node_name": test_name,
                "node_type": "Test",
                # alias, schema_name, materialization and bytes_processed are NULL
                # on the test branch of the union.
                "alias": "",
                "schema_name": "",
                "materialization": "",
                "status": status,
                "job_id": job_id or f"no-job:test:{test_exec_id}",
                "adapter_code": "OK" if status == "pass" else "ERROR",
                "message": "" if status == "pass" else f"Got {failures} result(s), expected 0",
                "rows_affected": "",
                "bytes_processed": "",
                "slot_ms": int(duration * 1000 * self.rng.uniform(1.5, 18.0)),
                "total_node_runtime": round(duration, 3),
                "compile_started_at": t_start.strftime("%Y-%m-%d %H:%M:%S"),
                "query_completed_at": t_end.strftime("%Y-%m-%d %H:%M:%S"),
                "run_started_at": run_start.strftime("%Y-%m-%d %H:%M:%S"),
                "run_date": run_start.date().isoformat(),
                "thread_id": f"Thread-{(thread_cycle % 8) + 1}",
                "was_full_refresh": "FALSE",
                "tags": "test",
                "test_node_id": f"test.{DBT_PROJECT}.{test_name}",
                "test_path": f"tests/generic/{test_name}.sql",
                "tested_nodes": f"model.{DBT_PROJECT}.{tested_model}",
                "failures": failures,
            })
            cursor = t_end

        # run-operation / on-run-end: labelled, but no node at all.
        if self.rng.random() < 0.4:
            hook_name, _, hook_gb = HOOK_SPECS[-1]
            self.add_job(
                when=cursor + timedelta(seconds=self.rng.uniform(1, 6)),
                user_email=DBT_SERVICE_ACCOUNT,
                statement_type="SCRIPT",
                processed=gb_jitter(hook_gb, self.rng, False),
                region=self.pick_region(),
                query=f"-- run-operation: {hook_name}",
                label_app="dbt",
                label_project=DBT_PROJECT,
                label_env=env,
                label_resource_type="",
                label_invocation_id=inv_id,
                is_dbt_job=True,
            )

    def emit_script_job(
        self, *, when: datetime, inv_id: str, env: str, model: str,
        processed: int, failed: bool, resource_type: str,
    ) -> str:
        """A multi-statement SCRIPT parent plus its children.

        The children each bill, and the parent reports the exact sum. Both rows
        land in the fact table, which is why cost has to be restricted to rows
        where parent_job_id is blank.
        """
        region = self.pick_region()
        # Children: build a temp table, then merge it in, then drop it.
        child_plan = [
            ("CREATE_TABLE_AS_SELECT", int(processed * 0.86), f"{model}__dbt_tmp"),
            ("MERGE", int(processed * 0.13), model),
            ("DROP_TABLE", 0, f"{model}__dbt_tmp"),
        ]
        child_billed_total = sum(billed_bytes(p, False) for _, p, _ in child_plan)
        total_duration = runtime_for(processed, self.rng)

        parent_id = self.add_job(
            when=when,
            user_email=DBT_SERVICE_ACCOUNT,
            statement_type="SCRIPT",
            processed=processed,
            region=region,
            destination_table_id=model,
            query=self.model_sql(model, "incremental", 3),
            label_app="dbt",
            label_project=DBT_PROJECT,
            label_env=env,
            label_model_name=model,
            label_resource_type=resource_type,
            label_invocation_id=inv_id,
            is_dbt_job=True,
            failed=failed,
            duration_override=total_duration,
            billed_override=None if failed else child_billed_total,
        )

        if failed:
            return parent_id

        offset = 0.0
        for (stmt, child_processed, dest) in child_plan:
            child_duration = max(0.2, total_duration * self.rng.uniform(0.15, 0.45))
            child_when = when + timedelta(seconds=offset)
            child_job = self.add_job(
                when=child_when,
                user_email=DBT_SERVICE_ACCOUNT,
                statement_type=stmt,
                processed=child_processed,
                region=region,
                parent_job_id=parent_id,
                # dbt's incremental temp-table suffix is stripped so the model
                # reads as one value rather than splitting off its temp table.
                destination_table_id=dest.replace("__dbt_tmp", ""),
                label_app="dbt",
                label_project=DBT_PROJECT,
                label_env=env,
                label_model_name=model,
                label_resource_type=resource_type,
                label_invocation_id=inv_id,
                is_dbt_job=True,
                duration_override=child_duration,
            )
            self.job_statements.append({
                "statement_job_id": child_job,
                "parent_job_id": parent_id,
                "statement_type": stmt,
                "start_time": child_when.strftime("%Y-%m-%d %H:%M:%S"),
                "end_time": (child_when + timedelta(seconds=child_duration)).strftime("%Y-%m-%d %H:%M:%S"),
                "duration_sec": int(round(child_duration)),
                "total_bytes_processed": child_processed,
                "total_bytes_billed": billed_bytes(child_processed, False),
                "total_slot_ms": int(child_duration * 1000 * self.rng.uniform(1.5, 22.0)),
                "error_message": "",
            })
            offset += child_duration
        return parent_id

    # -- non-dbt traffic ---------------------------------------------------- #

    def emit_adhoc(self, day: date, n: int) -> None:
        for _ in range(n):
            app, stmt, (lo, hi), is_service = self.rng.choice(ADHOC_PATTERNS)
            hour = self.rng.choices(
                population=list(range(24)),
                weights=[1, 1, 1, 1, 1, 2, 4, 8, 14, 18, 20, 19, 16, 18, 20, 19, 15, 10, 6, 4, 3, 2, 2, 1],
            )[0]
            when = datetime(day.year, day.month, day.day, hour,
                            self.rng.randint(0, 59), self.rng.randint(0, 59))
            processed = int(self.rng.uniform(lo, hi) * GB)
            # Repeated dashboard queries hit cache a good share of the time.
            cache_hit = is_service and self.rng.random() < 0.34
            bie_mode, bie_err = "DISABLED", None
            if app == "powerbi":
                roll = self.rng.random()
                if roll < 0.3:
                    bie_mode = "FULL"
                elif roll < 0.5:
                    bie_mode, bie_err = "PARTIAL", self.rng.choice(BIE_DECLINE)
                else:
                    bie_mode, bie_err = "DISABLED", self.rng.choice(BIE_DECLINE)

            self.add_job(
                when=when,
                user_email=BI_SERVICE_ACCOUNT if is_service else self.rng.choice(HUMAN_USERS),
                statement_type=stmt,
                processed=processed,
                region=self.pick_region(),
                cache_hit=cache_hit,
                destination_table_id=("(anonymous SELECT result)" if stmt == "SELECT"
                                      else f"scratch_{hash_hex(self.rng, 6)}"),
                query=self.adhoc_sql(app),
                label_app=app if is_service else "",
                is_dbt_job=False,
                failed=self.rng.random() < FAIL_RATE_ADHOC,
                bie_mode=bie_mode,
                bie_error=bie_err,
            )

    # -- SQL text ----------------------------------------------------------- #

    def model_sql(self, name: str, matz: str, upstream: int) -> str:
        refs = ", ".join(f"`{PROJECT}.analytics.stg_{s}`"
                         for s in self.rng.sample(
                             ["orders", "customers", "sessions", "impressions", "spend", "products"],
                             min(max(upstream, 1), 6)))
        if matz == "view":
            return f"CREATE OR REPLACE VIEW `{PROJECT}.analytics.{name}` AS\nSELECT * FROM {refs}"
        if matz == "incremental":
            return (f"MERGE `{PROJECT}.analytics.{name}` AS target\n"
                    f"USING (SELECT * FROM {refs} WHERE _PARTITIONTIME >= @start) AS source\n"
                    f"ON target.id = source.id\nWHEN MATCHED THEN UPDATE SET ...")
        return (f"CREATE OR REPLACE TABLE `{PROJECT}.analytics.{name}`\n"
                f"PARTITION BY DATE(event_ts) AS\nSELECT ... FROM {refs}\nGROUP BY 1, 2, 3")

    def adhoc_sql(self, app: str) -> str:
        tbl = self.rng.choice(["mart_revenue_daily", "mart_channel_perf", "fct_sessions",
                               "fct_orders", "mart_campaign_roi", "fct_impressions"])
        if app in ("powerbi", "looker"):
            return (f"SELECT channel, SUM(revenue) AS revenue, COUNT(*) AS n\n"
                    f"FROM `{PROJECT}.analytics.{tbl}`\n"
                    f"WHERE date BETWEEN @from AND @to\nGROUP BY 1 ORDER BY 2 DESC")
        return (f"SELECT *\nFROM `{PROJECT}.analytics.{tbl}`\n"
                f"WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL {self.rng.choice([7, 14, 30, 90])} DAY)\n"
                f"LIMIT {self.rng.choice([100, 1000, 10000])}")

    def pick_region(self) -> str:
        return self.rng.choices(REGIONS, weights=REGION_WEIGHTS)[0]

    # -- driver ------------------------------------------------------------- #

    def run(self) -> None:
        day = WINDOW_START
        while day < WINDOW_END:
            runs = RUNS_PER_WEEKDAY[day.weekday()]
            # Nightly prod build at 02:00, then non-prod runs through the working
            # day. dev/uat/prod is the promotion path most teams actually have.
            self.emit_invocation(
                datetime(day.year, day.month, day.day, 2, self.rng.randint(0, 20)),
                env="prod",
                command="build",
                # A full refresh on the first Sunday of the month, which is where
                # the month's cost spike comes from.
                full_refresh=(day.weekday() == 6 and day.day <= 7),
            )
            for i in range(runs - 1):
                hour = 9 + i * 3 + self.rng.randint(0, 2)
                self.emit_invocation(
                    datetime(day.year, day.month, day.day, min(hour, 21), self.rng.randint(0, 59)),
                    # Weighted 2:1 - developers rebuild far more often than a
                    # release candidate gets promoted to uat.
                    env=self.rng.choice(["dev", "dev", "uat"]),
                    command=self.rng.choices(["build", "run", "test"], weights=[5, 3, 2])[0],
                    full_refresh=False,
                )
            self.emit_adhoc(day, self.rng.randint(28, 62))
            day += timedelta(days=1)


# --------------------------------------------------------------------------- #
# Static dimension output
# --------------------------------------------------------------------------- #

def model_rows() -> tuple[list[dict], list[dict]]:
    """Rows for dbt_models and dbt_model_tags.

    Models and snapshots only - no tests. Both come from dbt_artifacts'
    dim_dbt__models, which does not hold tests; tests come from dim_dbt__tests and
    reach a layer by pointing their node_id at the model they test.
    """
    models, tags = [], []
    for (name, folder, subfolder, matz, upstream, scan_gb, tag_list) in MODEL_SPECS:
        node_id = node_id_for(name, matz)
        path = model_path(name, folder, subfolder)
        layer, sublayer = derive_layer_sublayer(path)
        models.append({
            "node_id": node_id,
            "model_name": name,
            "alias": name,
            "layer": layer,
            "sublayer": sublayer,
            "materialization": matz,
            "schema_name": "analytics",
            "database_name": PROJECT,
            "package_name": DBT_PROJECT,
            "path": path,
            "checksum": f"sha256:{name[:8]}",
            "tags": ", ".join(tag_list),
            "meta": "{}",
            "upstream_count": upstream,
        })
        for t in tag_list:
            tags.append({"node_id": node_id, "tag": t})
    return models, tags


def node_id_for(name: str, matz: str) -> str:
    return (f"snapshot.{DBT_PROJECT}.{name}" if matz == "snapshot"
            else f"model.{DBT_PROJECT}.{name}")


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #

JOB_COLUMNS = [
    "job_id", "creation_date", "creation_time", "start_time", "end_time",
    "duration_sec", "cache_hit", "creation_hour_24", "total_slot_hr",
    "DataSizeCategoryID", "total_bytes_processed", "total_bytes_billed",
    "error_reason", "error_location", "error_message", "user_email",
    "statement_type", "queryhash", "region", "bie_mode", "label_app",
    "label_project", "bie_error_code", "bie_error_reason", "label_env",
    "parent_job_id", "label_model_name", "label_resource_type",
    "label_invocation_id", "total_slot_ms", "destination_table_id", "query",
    "is_dbt_job",
]

INVOCATION_COLUMNS = [
    "command_invocation_id", "dbt_command", "dbt_vars", "dbt_version",
    "env_vars", "full_refresh_flag", "project_name", "run_date",
    "run_started_at", "target_name", "target_schema", "target_threads",
]

NODE_EXECUTION_COLUMNS = [
    "node_execution_id", "command_invocation_id", "node_id", "node_name",
    "node_type", "alias", "schema_name", "materialization", "status", "job_id",
    "adapter_code", "message", "rows_affected", "bytes_processed", "slot_ms",
    "total_node_runtime", "compile_started_at", "query_completed_at",
    "run_started_at", "run_date", "thread_id", "was_full_refresh", "tags",
    "test_node_id", "test_path", "tested_nodes", "failures",
]

MODEL_COLUMNS = [
    "node_id", "model_name", "alias", "layer", "sublayer", "materialization",
    "schema_name", "database_name", "package_name", "path", "checksum", "tags",
    "meta", "upstream_count",
]

STATEMENT_COLUMNS = [
    "statement_job_id", "parent_job_id", "statement_type", "start_time",
    "end_time", "duration_sec", "total_bytes_processed", "total_bytes_billed",
    "total_slot_ms", "error_message",
]


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})


def summarise(gen: Generator) -> dict:
    """Print the dataset's shape and return it as a dict.

    The returned figures get written to sample-data/summary.json and read by
    scripts/build_assets.py, so every number drawn on a README graphic comes from
    the same computation that produced the CSVs and cannot drift from them.
    """
    jobs = gen.jobs
    top = [j for j in jobs if not j["parent_job_id"]]
    children = [j for j in jobs if j["parent_job_id"]]
    dbt_top = [j for j in top if j["is_dbt_job"] == "TRUE"]

    def usd(rows: list[dict]) -> float:
        rate = {"eu": 6.125, "europe-west3": 8.125}
        return sum(int(r["total_bytes_billed"]) / TIB * rate[r["region"]] for r in rows)

    # Sentinel ids stand in for executions that never reached BigQuery, so they
    # must not be counted as matched to a real job.
    node_matched = {
        n["job_id"] for n in gen.node_executions
        if n["job_id"] and not n["job_id"].startswith("no-job:")
    }
    attributed = [j for j in dbt_top if j["job_id"] in node_matched]
    label_only = [j for j in dbt_top
                  if j["job_id"] not in node_matched and j["label_resource_type"]]
    run_level = [j for j in dbt_top
                 if j["job_id"] not in node_matched and not j["label_resource_type"]]

    print(f"  jobs                 {len(jobs):>7,}  ({len(top):,} top-level, {len(children):,} script children)")
    print(f"  dbt invocations      {len(gen.invocations):>7,}")
    print(f"  dbt node executions  {len(gen.node_executions):>7,}")
    print(f"  script statements    {len(gen.job_statements):>7,}")
    print()
    print(f"  total spend (top-level only)  ${usd(top):>10,.2f}")
    print(f"  double-count if children summed ${usd(top) + usd(children):>8,.2f}"
          f"   <- the trap the report avoids")
    print(f"  dbt spend                     ${usd(dbt_top):>10,.2f}")
    print()
    print("  dbt attribution mix (by spend):")
    for label, rows in (("Node (dbt metadata)", attributed),
                        ("Node (label)", label_only),
                        ("Run-level", run_level)):
        share = usd(rows) / usd(dbt_top) * 100 if dbt_top else 0
        print(f"    {label:<22} ${usd(rows):>9,.2f}  {share:>5.1f}%  ({len(rows):,} jobs)")

    cache = [j for j in top if j["cache_hit"] == "TRUE"]
    print()
    print(f"  cache hits           {len(cache):>7,}  ({len(cache) / len(top) * 100:.1f}% of top-level, $0 billed)")
    waste = sum(int(j["total_bytes_billed"]) - int(j["total_bytes_processed"])
                for j in top if int(j["total_bytes_billed"]) > int(j["total_bytes_processed"]))
    print(f"  minimum-billing waste {waste / GB:>6,.2f} GB billed but never scanned")

    node_exec = gen.node_executions
    failed_nodes = [n for n in node_exec if n["status"] in ("error", "fail")]
    skipped_nodes = [n for n in node_exec if n["status"] == "skipped"]

    return {
        "window_start": WINDOW_START.isoformat(),
        "window_end": (WINDOW_END - timedelta(days=1)).isoformat(),
        "jobs_total": len(jobs),
        "jobs_top_level": len(top),
        "jobs_script_children": len(children),
        "invocations": len(gen.invocations),
        "node_executions": len(node_exec),
        "script_statements": len(gen.job_statements),
        "models": len(MODEL_SPECS),
        "tests": len(TEST_SPECS),
        "users": len({j["user_email"] for j in jobs}),
        "spend_total": round(usd(top), 2),
        "spend_double_counted": round(usd(top) + usd(children), 2),
        "spend_dbt": round(usd(dbt_top), 2),
        "spend_non_dbt": round(usd(top) - usd(dbt_top), 2),
        "attribution": {
            "node_metadata": {"usd": round(usd(attributed), 2), "jobs": len(attributed)},
            "node_label": {"usd": round(usd(label_only), 2), "jobs": len(label_only)},
            "run_level": {"usd": round(usd(run_level), 2), "jobs": len(run_level)},
        },
        "cache_hits": len(cache),
        "cache_hit_pct": round(len(cache) / len(top) * 100, 1),
        "waste_gb": round(waste / GB, 2),
        "failed_nodes": len(failed_nodes),
        "skipped_nodes": len(skipped_nodes),
        "regions": REGIONS,
    }


def check_invariants(gen: Generator, models: list[dict]) -> None:
    """Assert the things the semantic model's relationships depend on.

    Each of these was a real failure at some point: a violation does not show up
    as bad numbers, it stops the refresh with a relationship error, so it is worth
    catching here rather than in Power BI.
    """
    problems = []

    # dbt_node_executions sits on the one side of the relationship to Fact, so its
    # job_id must be unique - blanks included, since blank counts as a value.
    ids = [n["job_id"] for n in gen.node_executions]
    if "" in ids:
        problems.append(f"{ids.count('')} node execution(s) have a blank job_id")
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        problems.append(f"{len(dupes)} duplicated job_id(s) in dbt_node_executions, "
                        f"e.g. {sorted(dupes)[:3]}")

    # dbt_models is the one side for node_id.
    node_ids = [m["node_id"] for m in models]
    if len(node_ids) != len(set(node_ids)):
        problems.append("duplicate node_id in dbt_models")

    # Every non-sentinel job_id on a node execution must exist in the fact table.
    job_ids = {j["job_id"] for j in gen.jobs}
    orphans = [n["job_id"] for n in gen.node_executions
               if not n["job_id"].startswith("no-job:") and n["job_id"] not in job_ids]
    if orphans:
        problems.append(f"{len(orphans)} node execution(s) reference a job_id "
                        f"absent from jobs.csv")

    # Every test row must reach a model row, or it inherits no layer.
    model_node_ids = {m["node_id"] for m in models}
    unreachable = {n["node_id"] for n in gen.node_executions
                   if n["node_type"] == "Test" and n["node_id"] not in model_node_ids}
    if unreachable:
        problems.append(f"{len(unreachable)} test row(s) point at a node_id with no "
                        f"dbt_models row")

    if problems:
        print("\n  INVARIANT FAILURES:")
        for p in problems:
            print(f"    - {p}")
        raise SystemExit(1)
    print("  invariants OK (job_id unique and non-blank, no orphans, tests reach models)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None, help="output directory (default: ../sample-data)")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    out = Path(args.out) if args.out else Path(__file__).resolve().parent.parent / "sample-data"
    out.mkdir(parents=True, exist_ok=True)

    print(f"Generating synthetic sample data (seed={args.seed}) ...")
    gen = Generator(args.seed)
    gen.run()
    models, tags = model_rows()
    check_invariants(gen, models)

    write_csv(out / "jobs.csv", JOB_COLUMNS, gen.jobs)
    write_csv(out / "dbt_invocations.csv", INVOCATION_COLUMNS, gen.invocations)
    write_csv(out / "dbt_node_executions.csv", NODE_EXECUTION_COLUMNS, gen.node_executions)
    write_csv(out / "dbt_models.csv", MODEL_COLUMNS, models)
    write_csv(out / "dbt_model_tags.csv", ["node_id", "tag"], tags)
    write_csv(out / "dbt_job_statements.csv", STATEMENT_COLUMNS, gen.job_statements)

    print()
    stats = summarise(gen)
    stats["seed"] = args.seed
    (out / "summary.json").write_bytes(
        (json.dumps(stats, indent=2) + "\n").encode("utf-8")
    )

    print()
    for f in sorted(out.glob("*.csv")):
        print(f"  wrote {f.name:<28} {f.stat().st_size / 1024:>8,.0f} KB")
    print(f"  wrote summary.json           "
          f"{(out / 'summary.json').stat().st_size / 1024:>8,.1f} KB  "
          f"(read by scripts/build_assets.py)")


if __name__ == "__main__":
    main()
