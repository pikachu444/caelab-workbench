"""Pure material calibration contracts and unit conversion layer (CORE01; lazy CORE02 exports)."""
from __future__ import annotations

from importlib import import_module

from .contracts import (
    AdapterIdentity,
    AssessmentResultV1,
    CampaignReceipt,
    CancelReceipt,
    CapabilityReport,
    ComparisonSpec,
    EvaluationReceipt,
    ExperimentChannelSpec,
    ExperimentPredictionV1,
    ExperimentSpec,
    ImportReceipt,
    ModelDescriptorV1,
    ObservableCapability,
    ObservableSpec,
    ParameterSpec,
    PredictedChannelV1,
    PredictionBundle,
    PredictionBundleV1,
    ScalarResponseSpecV1,
    SelectionRecordV1,
    StudySnapshot,
)
from .units import (
    QuantityUnitSpec,
    convert_frequency_angular,
    convert_values,
    parse_material_unit,
    validate_unit_string,
)

__all__ = [
    "AdapterIdentity",
    "AdapterRegistry",
    "ApplyPlan",
    "ApplyReceipt",
    "AssessmentResultV1",
    "CalibrationEvaluationArtifact",
    "CalibrationStudyService",
    "CampaignReceipt",
    "CancelReceipt",
    "CapabilityReport",
    "ComparisonSpec",
    "EvaluationPlan",
    "EvaluationReceipt",
    "ExperimentChannelSpec",
    "ExperimentPredictionV1",
    "ExperimentSpec",
    "ExportReceipt",
    "ImportReceipt",
    "MaterialAdapter",
    "MaterialEvaluationRequest",
    "MaterialExportPlan",
    "MaterialStudyError",
    "ModelDescriptorV1",
    "ObservableCapability",
    "ObservableSpec",
    "ParameterSpec",
    "PredictedChannelV1",
    "PredictionBundle",
    "PredictionBundleV1",
    "QuantityUnitSpec",
    "ReconcileReport",
    "RecoveryReceipt",
    "RecoveryReport",
    "RelinkReceipt",
    "SaveAsReceipt",
    "ScalarResponseSpecV1",
    "SelectionRecordV1",
    "StudySnapshot",
    "convert_frequency_angular",
    "convert_values",
    "default_registry",
    "import_csv_experiment",
    "parse_material_unit",
    "validate_unit_string",
]

_LAZY_EXPORTS = {
    "AdapterRegistry": ("caelab.materials.registry", "AdapterRegistry"),
    "ApplyPlan": ("caelab.materials.registry", "ApplyPlan"),
    "ApplyReceipt": ("caelab.materials.registry", "ApplyReceipt"),
    "CalibrationEvaluationArtifact": ("caelab.materials.registry", "CalibrationEvaluationArtifact"),
    "CalibrationStudyService": ("caelab.materials.service", "CalibrationStudyService"),
    "EvaluationPlan": ("caelab.materials.registry", "EvaluationPlan"),
    "ExportReceipt": ("caelab.materials.registry", "ExportReceipt"),
    "MaterialAdapter": ("caelab.materials.registry", "MaterialAdapter"),
    "MaterialEvaluationRequest": ("caelab.materials.registry", "MaterialEvaluationRequest"),
    "MaterialExportPlan": ("caelab.materials.registry", "MaterialExportPlan"),
    "MaterialStudyError": ("caelab.materials.registry", "MaterialStudyError"),
    "ReconcileReport": ("caelab.materials.registry", "ReconcileReport"),
    "RecoveryReceipt": ("caelab.materials.registry", "RecoveryReceipt"),
    "RecoveryReport": ("caelab.materials.registry", "RecoveryReport"),
    "RelinkReceipt": ("caelab.materials.registry", "RelinkReceipt"),
    "SaveAsReceipt": ("caelab.materials.registry", "SaveAsReceipt"),
    "default_registry": ("caelab.materials.registry", "default_registry"),
    "import_csv_experiment": ("caelab.materials.experiments", "import_csv_experiment"),
}


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_name, attr_name = target
    return getattr(import_module(mod_name), attr_name)
