from typing import Any, Self

from pydantic import BaseModel, Field

FLAG_FIELDS = ("has_elevator", "has_parking", "has_cellar", "has_balcony")


class DetailData(BaseModel):
    year_built: int | None = None
    last_renovation_year: int | None = None
    floor: int | None = None
    total_floors: int | None = None
    heating_type: str | None = None
    energy_source: str | None = None
    has_elevator: bool | None = None
    has_parking: bool | None = None
    has_cellar: bool | None = None
    has_balcony: bool | None = None
    balcony_count: int | None = None
    description: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    def fill_from(self, other: "DetailData") -> Self:
        """Fill missing values from `other`; a feature counts as present if either says so."""
        merged = self.model_copy()
        for name in type(self).model_fields:
            if name == "raw":
                continue
            mine, theirs = getattr(self, name), getattr(other, name)
            if name in FLAG_FIELDS and theirs is True:
                setattr(merged, name, True)
            elif mine is None:
                setattr(merged, name, theirs)
        if merged.has_balcony is False:
            merged.balcony_count = 0
        elif merged.has_balcony and not merged.balcony_count:
            several = self.raw.get("balcony_plural") or other.raw.get("balcony_plural")
            merged.balcony_count = other.balcony_count or self.balcony_count or (
                None if several else 1
            )
        return merged
