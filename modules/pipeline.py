from __future__ import annotations

from modules.aifa import compare_upc_with_aifa, reconcile_with_aifa
from modules.analytics import build_dataset_summary
from modules.normalizer import normalize_workbook
from modules.validator import validate_workbook


def process_workbook(
    workbook_data,
    schema,
    aifa_dir,
    use_holder_as_supplier=False,
):
    normalized_data, transformations, quality_issues = normalize_workbook(workbook_data, schema)

    (
        enriched_data,
        aifa_enrichments,
        aifa_issues,
        aifa_candidates,
        aifa_checks,
        supplier_proposals,
    ) = reconcile_with_aifa(
        normalized_data,
        schema,
        aifa_dir,
        use_holder_as_supplier=use_holder_as_supplier,
    )

    aifa_upc_checks, aifa_upc_issues = compare_upc_with_aifa(enriched_data, schema, aifa_dir)
    initial_issues = quality_issues + aifa_issues + aifa_upc_issues
    result = validate_workbook(enriched_data, schema, initial_issues=initial_issues)

    summary = build_dataset_summary(
        enriched_data,
        schema,
        transformations,
        enrichments=aifa_enrichments,
        upc_checks=aifa_upc_checks,
        aifa_checks=aifa_checks,
        supplier_proposals=supplier_proposals,
    )

    result["summary"] = summary
    result["transformations"] = transformations
    result["aifa_enrichments"] = aifa_enrichments
    result["aifa_upc_checks"] = aifa_upc_checks
    result["aifa_checks"] = aifa_checks
    result["supplier_proposals"] = supplier_proposals
    result["normalized_records"] = enriched_data.records

    return {
        "result": result,
        "data": enriched_data,
        "aifa_candidates": aifa_candidates,
    }
