from .base import ResultSet, SolverConverter, SupportTable
from .calculix import CalculiXConverter

CONVERTERS = {
    CalculiXConverter.id: CalculiXConverter(),
}


def get_converter(solver_id: str) -> SolverConverter:
    try:
        return CONVERTERS[solver_id]
    except KeyError:
        raise KeyError(f"unknown solver converter: {solver_id}") from None


__all__ = ["CONVERTERS", "ResultSet", "SolverConverter", "SupportTable", "get_converter"]
