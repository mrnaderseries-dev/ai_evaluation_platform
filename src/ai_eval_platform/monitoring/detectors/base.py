from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from ..signals import Signal


InputT = TypeVar("InputT", bound=BaseModel)
PolicyT = TypeVar("PolicyT", bound="DetectorPolicy")


class DetectorPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    minimum_samples: int = Field(default=1, ge=1)


class Detector(ABC, Generic[InputT, PolicyT]):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def detect(self, input_data: InputT) -> list[Signal]:
        raise NotImplementedError