from ..components.registry import ComponentRegistry
from ..components.types import ComponentType


class DependencyGraph:

    def __init__(
        self,
        registry: ComponentRegistry,
    ):
        self.registry = registry

    def dependencies_of(
        self,
        component: ComponentType,
    ) -> tuple[ComponentType, ...]:

        return self.registry.dependencies_of(component)

    def dependents_of(
        self,
        dependency: ComponentType,
    ) -> tuple[ComponentType, ...]:

        return tuple(
            component
            for component in self.registry.components()
            if dependency
            in self.registry.dependencies_of(component)
        )