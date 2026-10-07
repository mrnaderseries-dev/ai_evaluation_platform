from dataclasses import dataclass

from .types import ComponentType


@dataclass(frozen=True)
class ComponentDefinition:
    component: ComponentType
    dependencies: tuple[ComponentType, ...] = ()


class ComponentRegistry:
    def __init__(
        self,
        definitions: list[ComponentDefinition] | None = None,
    ):
        definitions = definitions or default_component_definitions()

        self._definitions = {
            definition.component: definition
            for definition in definitions
        }

    def get(self, component: ComponentType) -> ComponentDefinition:
        return self._definitions[component]

    def dependencies_of(
        self,
        component: ComponentType,
    ) -> tuple[ComponentType, ...]:
        return self.get(component).dependencies

    def components(self) -> tuple[ComponentType, ...]:
        return tuple(self._definitions)


def default_component_definitions() -> list[ComponentDefinition]:
    return [
        ComponentDefinition(
            ComponentType.API,
            (
                ComponentType.DATABASE,
                ComponentType.KAFKA,
                ComponentType.EXTERNAL_PROVIDER,
            ),
        ),
        ComponentDefinition(
            ComponentType.INGESTION,
            (
                ComponentType.KAFKA,
                ComponentType.DATABASE,
            ),
        ),
        ComponentDefinition(ComponentType.KAFKA),
        ComponentDefinition(ComponentType.DATABASE),
        ComponentDefinition(
            ComponentType.EVALUATION,
            (
                ComponentType.DATABASE,
                ComponentType.EXTERNAL_PROVIDER,
            ),
        ),
        ComponentDefinition(
            ComponentType.MONITORING,
            (ComponentType.DATABASE,),
        ),
        ComponentDefinition(
            ComponentType.BATCH_PIPELINE,
            (ComponentType.DATABASE,),
        ),
        ComponentDefinition(ComponentType.EXTERNAL_PROVIDER),
    ]