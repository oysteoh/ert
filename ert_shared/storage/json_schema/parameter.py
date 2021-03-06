from typing import Mapping
from pydantic import BaseModel

from ert_shared.storage.json_schema.prior import Prior


class ParameterBase(BaseModel):
    name: str
    group: str


class ParameterCreate(ParameterBase):
    realizations: Mapping[int, float]


class Parameter(ParameterBase):
    id: int
    prior: Prior

    class Config:
        orm_mode = True
