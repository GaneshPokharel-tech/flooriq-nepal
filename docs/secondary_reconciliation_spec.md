# Secondary Reconciliation Spec (v1)

Verified against the current repo state on 2026-03-20. This document freezes the current manual-only policy for secondary evidence reconciliation.

## 1. Scope and Non-Goals

This spec applies only to manual reconciliation for one `business_date` and one `source_name` at a time across the current primary path and the optional secondary provenance path.

In scope:
- The dormant provenance tables `public.acquisition_runs` and `public.raw_floorsheet_evidence`, which are created only by the explicit provenance migration flag and are not part of `supabase/schema.sql`.
- Append-only secondary CSV ingestion via `scripts/ingest_secondary_evidence.py`.
- Read-only coverage comparison via `scripts/compare_primary_vs_secondary.py`.
- Manual v1 classification as `pass`, `review-required`, or `fail`.

Out of scope:
- Any automatic repair, merge, replay, backfill, or recovery write into `public.raw_floorsheet` or `public.trades`.
- Any row-level or field-level conflict resolution between primary and secondary rows.
- Any change to the operational primary pipeline in `scripts/refresh.py` and `scripts/ingest_floorsheet.py`.
- Any change to the canonical runtime schema in `supabase/schema.sql`.

## 2. Authoritative Status of Primary vs Secondary Data

- `public.raw_floorsheet` is the authoritative raw operational table.
- `public.trades` is the authoritative normalized analytics table, and is derived only from `public.raw_floorsheet`.
- `scripts/ingest_floorsheet.py` treats the incoming primary CSV as the authoritative snapshot for one `business_date`, clears the existing `raw_floorsheet` and `trades` slice for that date, reloads `public.raw_floorsheet`, and then rebuilds `public.trades`.
- The current operational primary path is `scripts/refresh.py` -> `scripts/ingest_floorsheet.py`.
- `scripts/create_secondary_acquisition_tables.sql` explicitly states that the provenance tables do not alter current `raw_floorsheet` / `trades` semantics.
- `public.acquisition_runs` and `public.raw_floorsheet_evidence` are provenance-only secondary stores.
- `scripts/ingest_secondary_evidence.py` writes only to the provenance tables plus run status metadata.
- `scripts/compare_primary_vs_secondary.py` is read-only and prints metrics only.
- A secondary run is never canonical in v1, even when it fully matches the primary dataset.

## 3. Comparison Unit and Required Metrics

The v1 comparison unit is the latest succeeded acquisition run for one `source_name` and one `business_date`. `scripts/compare_primary_vs_secondary.py` intentionally selects the latest row in `public.acquisition_runs` where:

- `source_name = :source_name`
- `detected_business_date = :business_date`
- `status = 'succeeded'`

This matters because `public.raw_floorsheet_evidence` is append-only across runs, so older runs must not be double-counted after a rerun succeeds.

The required review metrics are the exact outputs from the comparison script:

- `secondary_run_id`: selected latest succeeded run id, or `none` if no comparable run exists.
- `primary_row_count`: `COUNT(*)` from `public.raw_floorsheet` for the target `business_date`.
- `secondary_row_count`: `COUNT(*)` from `public.raw_floorsheet_evidence` for the selected run, `source_name`, and `business_date`.
- `primary_distinct_contract_id_count`: distinct primary `contract_id` coverage for the target date.
- `secondary_distinct_contract_id_count`: distinct secondary `contract_id` coverage for the selected run, excluding `NULL` `contract_id`.
- `overlap_contract_count`: count of distinct `contract_id` values present in both sets.
- `primary_only_contract_count`: count of distinct `contract_id` values present only in primary.
- `secondary_only_contract_count`: count of distinct `contract_id` values present only in secondary.

Interpretation notes:

- In primary, `contract_id` is the table primary key, so `primary_row_count` and `primary_distinct_contract_id_count` should match.
- In secondary, uniqueness is enforced only on `(acquisition_run_id, contract_id)` when `contract_id IS NOT NULL`, so `secondary_row_count` can exceed `secondary_distinct_contract_id_count`.
- `overlap_contract_count`, `primary_only_contract_count`, and `secondary_only_contract_count` are contract-set metrics only. They do not compare non-key field values.
- `public.trades` is not compared separately in v1 because it is derived from `public.raw_floorsheet`, not an independent source of authority.

## 4. V1 Decision Policy

| Result | Criteria |
| --- | --- |
| `pass` | `secondary_run_id` is present; `primary_row_count > 0`; `secondary_row_count > 0`; `primary_row_count == secondary_row_count`; `primary_distinct_contract_id_count == secondary_distinct_contract_id_count`; `overlap_contract_count == primary_distinct_contract_id_count == secondary_distinct_contract_id_count`; `primary_only_contract_count == 0`; and `secondary_only_contract_count == 0`. |
| `review-required` | `secondary_run_id` is present and both sides are non-empty, but any `pass` criterion fails. This includes any row-count mismatch, any distinct-coverage mismatch, any non-zero `primary_only_contract_count`, any non-zero `secondary_only_contract_count`, or any partial overlap. |
| `fail` | No comparable succeeded run exists (`secondary_run_id = none`); either side is empty (`primary_row_count = 0` or `secondary_row_count = 0`); or the contract sets do not intersect at all (`overlap_contract_count = 0`). |

Only `pass` means the secondary run is coverage-equivalent to the current primary slice for v1. Even then, `pass` does not authorize canonical writes.

Both `review-required` and `fail` block any recovery/backfill use under this spec until there is separate human investigation and a separately reviewed procedure.

## 5. No Auto-Promotion Rules

- Secondary evidence MUST NOT be inserted into, merged into, or used to delete from `public.raw_floorsheet`.
- Secondary evidence MUST NOT be inserted into or used to rebuild `public.trades` in place of the primary path.
- `scripts/ingest_secondary_evidence.py` MUST remain provenance-write-only: `public.acquisition_runs` plus `public.raw_floorsheet_evidence`.
- `scripts/compare_primary_vs_secondary.py` MUST remain read-only.
- No existing verified script reads from `public.raw_floorsheet_evidence` when populating `public.raw_floorsheet` or `public.trades`.
- Any future promotion, recovery, or backfill mechanism requires a separate reviewed design and explicit code changes; it is not part of v1.

## 6. Required Metadata and Provenance Retention

The following run-level provenance MUST always be retained in `public.acquisition_runs`:

- `id`, `run_key`, `source_name`, `source_kind`, `acquisition_mode`
- `workflow_ref`, `requested_business_date`, `detected_business_date`
- `status`, `row_count`, `notes`
- `metadata`
- `started_at`, `completed_at`, `created_at`

The following current ingester metadata MUST be preserved when present:

- `metadata.ingest_script`
- `metadata.csv_path`
- `metadata.error_message` on failed runs

The following row-level provenance MUST always be retained in `public.raw_floorsheet_evidence`:

- `id`, `acquisition_run_id`, `source_name`, `source_kind`, `business_date`, `contract_id`
- `stock_symbol`, `buyer_member_id`, `seller_member_id`, `contract_quantity`, `contract_rate`, `contract_amount`
- `trade_book_id`, `stock_id`, `buyer_broker_name`, `seller_broker_name`, `trade_time`, `security_name`
- `source_record_hash`
- `raw_payload`
- `metadata`
- `created_at`

The following current row metadata MUST be preserved when present:

- `metadata.csv_row_number`

`raw_payload` must remain the exact ingested row payload. Reconciliation must not strip it, replace it with a normalized subset, or detach evidence rows from their `acquisition_run_id`.

## 7. Current Manual Workflow

1. Ensure the target primary date already exists in `public.raw_floorsheet` via the normal primary path. In the current repo, that means running `scripts/ingest_floorsheet.py` directly or `scripts/refresh.py`. Secondary reconciliation does not replace the primary ingest.
2. If the provenance tables have not been created in the target database, create them explicitly:

   ```bash
   PYTHONPATH=. python scripts/run_migration.py --include-secondary-acquisition-provenance
   ```

3. Ingest a secondary CSV into provenance only. The ingester requires a header with `businessDate` or `business_date`, enforces exactly one `business_date` per CSV, and can enforce `--requested-business-date` when supplied:

   ```bash
   PYTHONPATH=. python scripts/ingest_secondary_evidence.py \
     --csv path/to/secondary.csv \
     --run-key SOURCE_NAME-YYYY-MM-DD-recovery-001 \
     --source-name SOURCE_NAME \
     --source-kind archive \
     --acquisition-mode recovery \
     --requested-business-date YYYY-MM-DD \
     --workflow-ref manual-YYYY-MM-DD
   ```

   Use the actual `source_kind` (`api|scrape|archive|manual`) and `acquisition_mode` (`daily|backfill|recovery|replay|compare`) for the run.

4. Compare the primary slice against the latest succeeded secondary run for the same `source_name` and `business_date`:

   ```bash
   PYTHONPATH=. python scripts/compare_primary_vs_secondary.py \
     --business-date YYYY-MM-DD \
     --source-name SOURCE_NAME
   ```

5. Review all emitted metrics from step 4 and classify the result using Section 4.
6. Stop there. Under v1 there is no approved or implemented script that promotes secondary evidence into `public.raw_floorsheet` or `public.trades`.
