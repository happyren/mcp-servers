"""Data models for coding convention storage."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ProgrammingLanguage(str, Enum):
    """Supported programming languages."""

    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    JAVA = "java"
    GO = "go"
    RUST = "rust"
    CPP = "cpp"
    CSHARP = "csharp"
    RUBY = "ruby"
    PHP = "php"
    SWIFT = "swift"
    KOTLIN = "kotlin"
    SCALA = "scala"
    DART = "dart"
    UNKNOWN = "unknown"


class ConventionCategory(str, Enum):
    """Categories of coding conventions."""

    NAMING = "naming"
    FORMATTING = "formatting"
    STRUCTURE = "structure"
    DOCUMENTATION = "documentation"
    ERROR_HANDLING = "error_handling"
    TESTING = "testing"
    PERFORMANCE = "performance"
    SECURITY = "security"
    OTHER = "other"


class ConventionSeverity(str, Enum):
    """Severity levels for conventions."""

    REQUIRED = "required"
    RECOMMENDED = "recommended"
    SUGGESTED = "suggested"
    OPTIONAL = "optional"


class CodeConvention(BaseModel):
    """A single coding convention rule."""

    id: str = Field(..., description="Unique identifier for the convention")
    language: ProgrammingLanguage = Field(..., description="Programming language")
    category: ConventionCategory = Field(..., description="Category of convention")
    severity: ConventionSeverity = Field(
        default=ConventionSeverity.RECOMMENDED, description="Importance level"
    )
    pattern: str | None = Field(default=None, description="Regex pattern for detection")
    description: str = Field(..., description="Human-readable description")
    example: str | None = Field(default=None, description="Example of compliant code")
    counter_example: str | None = Field(default=None, description="Example of non-compliant code")
    source_repository: str | None = Field(
        default=None, description="Repository where this convention was observed"
    )
    created_at: datetime = Field(
        default_factory=datetime.now, description="When this convention was added"
    )
    updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score (0.0-1.0)")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class RepositoryAnalysis(BaseModel):
    """Analysis results for a repository."""

    id: str = Field(..., description="Unique identifier for analysis")
    repository_url: str = Field(..., description="Repository URL or path")
    repository_name: str = Field(..., description="Repository name")
    analyzed_at: datetime = Field(
        default_factory=datetime.now, description="When analysis was performed"
    )
    languages_used: list[ProgrammingLanguage] = Field(
        default_factory=list, description="Languages detected"
    )
    conventions_found: list[str] = Field(
        default_factory=list, description="IDs of conventions found"
    )
    convention_summary: dict[ConventionCategory, int] = Field(
        default_factory=dict, description="Count by category"
    )
    compliance_score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Overall compliance score"
    )
    file_count: int = Field(default=0, description="Number of files analyzed")
    analysis_metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional analysis metadata"
    )


class ComparisonResult(BaseModel):
    """Result of comparing two repositories or conventions."""

    id: str = Field(..., description="Unique identifier for comparison")
    repository_a: str = Field(..., description="First repository name or ID")
    repository_b: str = Field(..., description="Second repository name or ID")
    compared_at: datetime = Field(
        default_factory=datetime.now, description="When comparison was performed"
    )
    common_conventions: list[str] = Field(
        default_factory=list, description="Conventions present in both"
    )
    unique_to_a: list[str] = Field(
        default_factory=list, description="Conventions only in repository A"
    )
    unique_to_b: list[str] = Field(
        default_factory=list, description="Conventions only in repository B"
    )
    similarity_score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Similarity score (0-1)"
    )
    recommendations: list[str] = Field(
        default_factory=list, description="Recommendations for alignment"
    )
