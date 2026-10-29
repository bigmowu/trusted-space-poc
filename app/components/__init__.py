"""可观测、可自检的组件中心。"""

from app.components.registry import component_detail, list_components, run_self_test

__all__ = ["component_detail", "list_components", "run_self_test"]
