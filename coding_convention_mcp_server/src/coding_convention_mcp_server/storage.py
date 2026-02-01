"""Persistent storage for coding conventions using SQLite or JSON."""

import json
import logging
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .config import get_settings
from .models import (
    CodeConvention,
    ComparisonResult,
    ConventionCategory,
    ConventionSeverity,
    ProgrammingLanguage,
    RepositoryAnalysis,
)

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """Base exception for storage errors."""

    pass


class ConventionStorage:
    """Abstract base class for convention storage."""

    def __init__(self):
        self.settings = get_settings()

    def save_convention(self, convention: CodeConvention) -> str:
        """Save a coding convention."""
        raise NotImplementedError

    def get_convention(self, convention_id: str) -> CodeConvention | None:
        """Get a coding convention by ID."""
        raise NotImplementedError

    def get_conventions(
        self,
        language: ProgrammingLanguage | None = None,
        category: ConventionCategory | None = None,
        severity: ConventionSeverity | None = None,
        limit: int = 100,
    ) -> list[CodeConvention]:
        """Get conventions with optional filters."""
        raise NotImplementedError

    def update_convention(self, convention_id: str, updates: dict[str, Any]) -> bool:
        """Update a convention."""
        raise NotImplementedError

    def delete_convention(self, convention_id: str) -> bool:
        """Delete a convention."""
        raise NotImplementedError

    def save_analysis(self, analysis: RepositoryAnalysis) -> str:
        """Save repository analysis."""
        raise NotImplementedError

    def get_analysis(self, analysis_id: str) -> RepositoryAnalysis | None:
        """Get analysis by ID."""
        raise NotImplementedError

    def get_repository_analyses(self, repository_url: str) -> list[RepositoryAnalysis]:
        """Get all analyses for a repository."""
        raise NotImplementedError

    def save_comparison(self, comparison: ComparisonResult) -> str:
        """Save comparison result."""
        raise NotImplementedError

    def get_comparison(self, comparison_id: str) -> ComparisonResult | None:
        """Get comparison by ID."""
        raise NotImplementedError


class SQLiteStorage(ConventionStorage):
    """SQLite implementation of convention storage."""

    def __init__(self, db_path: str | Path | None = None):
        super().__init__()

        # Determine final path
        if db_path is None:
            storage_dir = Path(self.settings.storage_path).expanduser()
            storage_dir.mkdir(parents=True, exist_ok=True)
            final_path = storage_dir / "conventions.db"
        elif isinstance(db_path, str):
            final_path = Path(db_path)
        else:
            final_path = db_path  # db_path is already a Path

        self.db_path: Path = final_path
        self._init_db()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection with proper settings."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        """Initialize database tables."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Conventions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conventions (
                    id TEXT PRIMARY KEY,
                    language TEXT NOT NULL,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    pattern TEXT,
                    description TEXT NOT NULL,
                    example TEXT,
                    counter_example TEXT,
                    source_repository TEXT,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    confidence REAL NOT NULL,
                    metadata TEXT NOT NULL
                )
            """)

            # Repository analyses table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS repository_analyses (
                    id TEXT PRIMARY KEY,
                    repository_url TEXT NOT NULL,
                    repository_name TEXT NOT NULL,
                    analyzed_at TIMESTAMP NOT NULL,
                    languages_used TEXT NOT NULL,
                    conventions_found TEXT NOT NULL,
                    convention_summary TEXT NOT NULL,
                    compliance_score REAL NOT NULL,
                    file_count INTEGER NOT NULL,
                    analysis_metadata TEXT NOT NULL
                )
            """)

            # Comparisons table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS comparisons (
                    id TEXT PRIMARY KEY,
                    repository_a TEXT NOT NULL,
                    repository_b TEXT NOT NULL,
                    compared_at TIMESTAMP NOT NULL,
                    common_conventions TEXT NOT NULL,
                    unique_to_a TEXT NOT NULL,
                    unique_to_b TEXT NOT NULL,
                    similarity_score REAL NOT NULL,
                    recommendations TEXT NOT NULL
                )
            """)

            conn.commit()

    def save_convention(self, convention: CodeConvention) -> str:
        """Save a coding convention."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Convert datetime to ISO format string for SQLite
            created_at = convention.created_at.isoformat()
            updated_at = convention.updated_at.isoformat()

            cursor.execute(
                """
                INSERT OR REPLACE INTO conventions 
                (id, language, category, severity, pattern, description, example, 
                 counter_example, source_repository, created_at, updated_at, 
                 confidence, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    convention.id,
                    convention.language.value,
                    convention.category.value,
                    convention.severity.value,
                    convention.pattern,
                    convention.description,
                    convention.example,
                    convention.counter_example,
                    convention.source_repository,
                    created_at,
                    updated_at,
                    convention.confidence,
                    json.dumps(convention.metadata),
                ),
            )

            conn.commit()
            return convention.id

    def get_convention(self, convention_id: str) -> CodeConvention | None:
        """Get a coding convention by ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM conventions WHERE id = ?", (convention_id,))
            row = cursor.fetchone()

            if not row:
                return None

            return self._row_to_convention(row)

    def get_conventions(
        self,
        language: ProgrammingLanguage | None = None,
        category: ConventionCategory | None = None,
        severity: ConventionSeverity | None = None,
        limit: int = 100,
    ) -> list[CodeConvention]:
        """Get conventions with optional filters."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            query = "SELECT * FROM conventions WHERE 1=1"
            params = []

            if language:
                query += " AND language = ?"
                params.append(language.value)

            if category:
                query += " AND category = ?"
                params.append(category.value)

            if severity:
                query += " AND severity = ?"
                params.append(severity.value)

            query += " ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()

            return [self._row_to_convention(row) for row in rows]

    def update_convention(self, convention_id: str, updates: dict[str, Any]) -> bool:
        """Update a convention."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Check if convention exists
            cursor.execute("SELECT id FROM conventions WHERE id = ?", (convention_id,))
            if not cursor.fetchone():
                return False

            # Build update query
            set_clauses = []
            params = []

            for key, value in updates.items():
                if key == "language" and isinstance(value, ProgrammingLanguage):
                    value = value.value
                elif key == "category" and isinstance(value, ConventionCategory):
                    value = value.value
                elif key == "severity" and isinstance(value, ConventionSeverity):
                    value = value.value
                elif key == "metadata" and isinstance(value, dict):
                    value = json.dumps(value)
                elif key in ("created_at", "updated_at") and isinstance(value, datetime):
                    value = value.isoformat()

                set_clauses.append(f"{key} = ?")
                params.append(value)

            # Always update updated_at
            if "updated_at" not in updates:
                set_clauses.append("updated_at = ?")
                params.append(datetime.now().isoformat())

            params.append(convention_id)

            query = f"UPDATE conventions SET {', '.join(set_clauses)} WHERE id = ?"
            cursor.execute(query, params)
            conn.commit()

            return cursor.rowcount > 0

    def delete_convention(self, convention_id: str) -> bool:
        """Delete a convention."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM conventions WHERE id = ?", (convention_id,))
            conn.commit()
            return cursor.rowcount > 0

    def _row_to_convention(self, row: sqlite3.Row) -> CodeConvention:
        """Convert SQLite row to CodeConvention object."""
        return CodeConvention(
            id=row["id"],
            language=ProgrammingLanguage(row["language"]),
            category=ConventionCategory(row["category"]),
            severity=ConventionSeverity(row["severity"]),
            pattern=row["pattern"],
            description=row["description"],
            example=row["example"],
            counter_example=row["counter_example"],
            source_repository=row["source_repository"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            confidence=row["confidence"],
            metadata=json.loads(row["metadata"]),
        )

    def save_analysis(self, analysis: RepositoryAnalysis) -> str:
        """Save repository analysis."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            analyzed_at = analysis.analyzed_at.isoformat()
            languages_used = json.dumps([lang.value for lang in analysis.languages_used])
            conventions_found = json.dumps(analysis.conventions_found)
            convention_summary = json.dumps(
                {k.value: v for k, v in analysis.convention_summary.items()}
            )
            analysis_metadata = json.dumps(analysis.analysis_metadata)

            cursor.execute(
                """
                INSERT OR REPLACE INTO repository_analyses 
                (id, repository_url, repository_name, analyzed_at, languages_used,
                 conventions_found, convention_summary, compliance_score, file_count, analysis_metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    analysis.id,
                    analysis.repository_url,
                    analysis.repository_name,
                    analyzed_at,
                    languages_used,
                    conventions_found,
                    convention_summary,
                    analysis.compliance_score,
                    analysis.file_count,
                    analysis_metadata,
                ),
            )

            conn.commit()
            return analysis.id

    def get_analysis(self, analysis_id: str) -> RepositoryAnalysis | None:
        """Get analysis by ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM repository_analyses WHERE id = ?", (analysis_id,))
            row = cursor.fetchone()

            if not row:
                return None

            return self._row_to_analysis(row)

    def get_repository_analyses(self, repository_url: str) -> list[RepositoryAnalysis]:
        """Get all analyses for a repository."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM repository_analyses WHERE repository_url = ? ORDER BY analyzed_at DESC",
                (repository_url,),
            )
            rows = cursor.fetchall()

            return [self._row_to_analysis(row) for row in rows]

    def _row_to_analysis(self, row: sqlite3.Row) -> RepositoryAnalysis:
        """Convert SQLite row to RepositoryAnalysis object."""
        return RepositoryAnalysis(
            id=row["id"],
            repository_url=row["repository_url"],
            repository_name=row["repository_name"],
            analyzed_at=datetime.fromisoformat(row["analyzed_at"]),
            languages_used=[
                ProgrammingLanguage(lang) for lang in json.loads(row["languages_used"])
            ],
            conventions_found=json.loads(row["conventions_found"]),
            convention_summary={
                ConventionCategory(k): v for k, v in json.loads(row["convention_summary"]).items()
            },
            compliance_score=row["compliance_score"],
            file_count=row["file_count"],
            analysis_metadata=json.loads(row["analysis_metadata"]),
        )

    def save_comparison(self, comparison: ComparisonResult) -> str:
        """Save comparison result."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            compared_at = comparison.compared_at.isoformat()
            common_conventions = json.dumps(comparison.common_conventions)
            unique_to_a = json.dumps(comparison.unique_to_a)
            unique_to_b = json.dumps(comparison.unique_to_b)
            recommendations = json.dumps(comparison.recommendations)

            cursor.execute(
                """
                INSERT OR REPLACE INTO comparisons 
                (id, repository_a, repository_b, compared_at, common_conventions,
                 unique_to_a, unique_to_b, similarity_score, recommendations)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    comparison.id,
                    comparison.repository_a,
                    comparison.repository_b,
                    compared_at,
                    common_conventions,
                    unique_to_a,
                    unique_to_b,
                    comparison.similarity_score,
                    recommendations,
                ),
            )

            conn.commit()
            return comparison.id

    def get_comparison(self, comparison_id: str) -> ComparisonResult | None:
        """Get comparison by ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM comparisons WHERE id = ?", (comparison_id,))
            row = cursor.fetchone()

            if not row:
                return None

            return ComparisonResult(
                id=row["id"],
                repository_a=row["repository_a"],
                repository_b=row["repository_b"],
                compared_at=datetime.fromisoformat(row["compared_at"]),
                common_conventions=json.loads(row["common_conventions"]),
                unique_to_a=json.loads(row["unique_to_a"]),
                unique_to_b=json.loads(row["unique_to_b"]),
                similarity_score=row["similarity_score"],
                recommendations=json.loads(row["recommendations"]),
            )


class JSONStorage(ConventionStorage):
    """JSON file implementation of convention storage."""

    def __init__(self, json_path: str | Path | None = None):
        super().__init__()

        # Handle None case
        if json_path is None:
            storage_dir = Path(self.settings.storage_path).expanduser()
            storage_dir.mkdir(parents=True, exist_ok=True)
            json_path = storage_dir / "conventions.json"

        # Convert string to Path if needed
        if isinstance(json_path, str):
            json_path = Path(json_path)

        # At this point, json_path is definitely a Path
        self.json_path: Path = json_path
        self._data = self._load_data()

    def _load_data(self) -> dict[str, Any]:
        """Load data from JSON file."""
        if not self.json_path.exists():
            return {"conventions": [], "analyses": [], "comparisons": []}

        try:
            with open(self.json_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {"conventions": [], "analyses": [], "comparisons": []}

    def _save_data(self):
        """Save data to JSON file."""
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, default=str)

    def save_convention(self, convention: CodeConvention) -> str:
        """Save a coding convention."""
        # Convert to dict
        convention_dict = convention.dict()
        convention_dict["created_at"] = convention.created_at.isoformat()
        convention_dict["updated_at"] = convention.updated_at.isoformat()

        # Check if convention exists
        for i, existing in enumerate(self._data["conventions"]):
            if existing["id"] == convention.id:
                self._data["conventions"][i] = convention_dict
                break
        else:
            self._data["conventions"].append(convention_dict)

        self._save_data()
        return convention.id

    def get_convention(self, convention_id: str) -> CodeConvention | None:
        """Get a coding convention by ID."""
        for convention_dict in self._data["conventions"]:
            if convention_dict["id"] == convention_id:
                return self._dict_to_convention(convention_dict)
        return None

    def get_conventions(
        self,
        language: ProgrammingLanguage | None = None,
        category: ConventionCategory | None = None,
        severity: ConventionSeverity | None = None,
        limit: int = 100,
    ) -> list[CodeConvention]:
        """Get conventions with optional filters."""
        filtered = []

        for convention_dict in self._data["conventions"]:
            if language and convention_dict["language"] != language.value:
                continue
            if category and convention_dict["category"] != category.value:
                continue
            if severity and convention_dict["severity"] != severity.value:
                continue
            filtered.append(self._dict_to_convention(convention_dict))

        # Sort by updated_at descending
        filtered.sort(key=lambda x: x.updated_at, reverse=True)
        return filtered[:limit]

    def _dict_to_convention(self, convention_dict: dict[str, Any]) -> CodeConvention:
        """Convert dict to CodeConvention object."""
        # Convert string enums back to enum values
        convention_dict["language"] = ProgrammingLanguage(convention_dict["language"])
        convention_dict["category"] = ConventionCategory(convention_dict["category"])
        convention_dict["severity"] = ConventionSeverity(convention_dict["severity"])

        # Convert ISO strings back to datetime
        if isinstance(convention_dict["created_at"], str):
            convention_dict["created_at"] = datetime.fromisoformat(convention_dict["created_at"])
        if isinstance(convention_dict["updated_at"], str):
            convention_dict["updated_at"] = datetime.fromisoformat(convention_dict["updated_at"])

        return CodeConvention(**convention_dict)

    def update_convention(self, convention_id: str, updates: dict[str, Any]) -> bool:
        """Update a convention."""
        for i, convention_dict in enumerate(self._data["conventions"]):
            if convention_dict["id"] == convention_id:
                # Apply updates
                for key, value in updates.items():
                    if key in ("language", "category", "severity") and isinstance(value, Enum):
                        value = value.value
                    elif key in ("created_at", "updated_at") and isinstance(value, datetime):
                        value = value.isoformat()
                    elif key == "metadata" and isinstance(value, dict):
                        value = value

                    convention_dict[key] = value

                # Always update updated_at
                if "updated_at" not in updates:
                    convention_dict["updated_at"] = datetime.now().isoformat()

                self._save_data()
                return True

        return False

    def delete_convention(self, convention_id: str) -> bool:
        """Delete a convention."""
        initial_length = len(self._data["conventions"])
        self._data["conventions"] = [
            c for c in self._data["conventions"] if c["id"] != convention_id
        ]

        if len(self._data["conventions"]) < initial_length:
            self._save_data()
            return True
        return False

    def save_analysis(self, analysis: RepositoryAnalysis) -> str:
        """Save repository analysis."""
        analysis_dict = analysis.dict()
        analysis_dict["analyzed_at"] = analysis.analyzed_at.isoformat()
        analysis_dict["languages_used"] = [lang.value for lang in analysis.languages_used]
        analysis_dict["convention_summary"] = {
            k.value: v for k, v in analysis.convention_summary.items()
        }

        # Check if analysis exists
        for i, existing in enumerate(self._data["analyses"]):
            if existing["id"] == analysis.id:
                self._data["analyses"][i] = analysis_dict
                break
        else:
            self._data["analyses"].append(analysis_dict)

        self._save_data()
        return analysis.id

    def get_analysis(self, analysis_id: str) -> RepositoryAnalysis | None:
        """Get analysis by ID."""
        for analysis_dict in self._data["analyses"]:
            if analysis_dict["id"] == analysis_id:
                return self._dict_to_analysis(analysis_dict)
        return None

    def get_repository_analyses(self, repository_url: str) -> list[RepositoryAnalysis]:
        """Get all analyses for a repository."""
        analyses = []
        for analysis_dict in self._data["analyses"]:
            if analysis_dict["repository_url"] == repository_url:
                analyses.append(self._dict_to_analysis(analysis_dict))

        analyses.sort(key=lambda x: x.analyzed_at, reverse=True)
        return analyses

    def _dict_to_analysis(self, analysis_dict: dict[str, Any]) -> RepositoryAnalysis:
        """Convert dict to RepositoryAnalysis object."""
        # Convert string enums back to enum values
        analysis_dict["languages_used"] = [
            ProgrammingLanguage(lang) for lang in analysis_dict["languages_used"]
        ]
        analysis_dict["convention_summary"] = {
            ConventionCategory(k): v for k, v in analysis_dict["convention_summary"].items()
        }

        # Convert ISO string back to datetime
        if isinstance(analysis_dict["analyzed_at"], str):
            analysis_dict["analyzed_at"] = datetime.fromisoformat(analysis_dict["analyzed_at"])

        return RepositoryAnalysis(**analysis_dict)

    def save_comparison(self, comparison: ComparisonResult) -> str:
        """Save comparison result."""
        comparison_dict = comparison.dict()
        comparison_dict["compared_at"] = comparison.compared_at.isoformat()

        # Check if comparison exists
        for i, existing in enumerate(self._data["comparisons"]):
            if existing["id"] == comparison.id:
                self._data["comparisons"][i] = comparison_dict
                break
        else:
            self._data["comparisons"].append(comparison_dict)

        self._save_data()
        return comparison.id

    def get_comparison(self, comparison_id: str) -> ComparisonResult | None:
        """Get comparison by ID."""
        for comparison_dict in self._data["comparisons"]:
            if comparison_dict["id"] == comparison_id:
                return self._dict_to_comparison(comparison_dict)
        return None

    def _dict_to_comparison(self, comparison_dict: dict[str, Any]) -> ComparisonResult:
        """Convert dict to ComparisonResult object."""
        # Convert ISO string back to datetime
        if isinstance(comparison_dict["compared_at"], str):
            comparison_dict["compared_at"] = datetime.fromisoformat(comparison_dict["compared_at"])

        return ComparisonResult(**comparison_dict)


def get_storage() -> ConventionStorage:
    """Get the appropriate storage implementation based on settings."""
    settings = get_settings()

    if settings.storage_type.lower() == "json":
        return JSONStorage()
    else:
        return SQLiteStorage()
