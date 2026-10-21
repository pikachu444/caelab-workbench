"""CAE Lab install helper: manifest-driven program installer."""

from .engine import Installer, ProgramResult, load_manifest

__all__ = ["Installer", "ProgramResult", "load_manifest"]
