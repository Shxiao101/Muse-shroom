"""Local read-only Explorer over persisted search sessions."""

from .read_model import ExplorerReadModel
from .server import run_explorer

__all__ = ["ExplorerReadModel", "run_explorer"]
