"""Pydantic models for entity configuration."""

from pydantic import BaseModel, Field


class Entity(BaseModel):
    """Model for an individual entity with variants."""
    id: str
    variants: list[str]
    pattern: str | None = None  # Optional pattern for numbers


class Config(BaseModel):
    """Overall configuration model for entities."""
    person: list[Entity] = Field(alias="PERSON", default_factory=list)
    email_address: list[Entity] = Field(alias="EMAIL_ADDRESS", default_factory=list)
    location: list[Entity] = Field(alias="LOCATION", default_factory=list)
    number: list[Entity] = Field(alias="NUMBER", default_factory=list)
    cpr_number: list[Entity] = Field(alias="CPR_NUMBER", default_factory=list)
