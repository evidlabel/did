"""Pydantic models for entity configuration."""

from pydantic import BaseModel, Field

class Entity(BaseModel):
    """Model for an individual entity with variants."""
    id: str
    variants: list[str]
    pattern: str | None = None  # Optional pattern for numbers

class Config(BaseModel):
    """Overall configuration model for entities."""
    names: list[Entity] = Field(default_factory=list)
    emails: list[Entity] = Field(default_factory=list)
    addresses: list[Entity] = Field(default_factory=list)
    numbers: list[Entity] = Field(default_factory=list)
    cpr: list[Entity] = Field(default_factory=list)
