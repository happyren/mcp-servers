"""Repository analysis for extracting coding conventions."""

import ast
import logging
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .models import CodeConvention, ConventionCategory, ConventionSeverity, ProgrammingLanguage
from .storage import get_storage

logger = logging.getLogger(__name__)


class RepositoryAnalyzer:
    """Analyze repositories to extract coding conventions."""

    def __init__(self, max_files: int = 100):
        self.max_files = max_files
        self.storage = get_storage()

    def analyze(self, repository_path: str) -> dict[str, Any]:
        """Analyze a repository and return analysis results."""
        logger.info(f"Analyzing repository: {repository_path}")

        # Determine if path is local directory or Git URL
        if self._is_git_url(repository_path):
            return self._analyze_git_repo(repository_path)
        else:
            return self._analyze_local_dir(repository_path)

    def _is_git_url(self, path: str) -> bool:
        """Check if path looks like a Git URL."""
        return path.startswith(("http://", "https://", "git://", "git@"))

    def _analyze_git_repo(self, git_url: str) -> dict[str, Any]:
        """Clone and analyze a Git repository."""
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                # Clone repository
                logger.info(f"Cloning repository: {git_url}")
                result = subprocess.run(
                    ["git", "clone", "--depth", "1", git_url, temp_dir],
                    capture_output=True,
                    text=True,
                )

                if result.returncode != 0:
                    return {
                        "error": f"Failed to clone repository: {result.stderr}",
                        "conventions": [],
                        "languages": [],
                        "file_count": 0,
                    }

                return self._analyze_local_dir(temp_dir, original_url=git_url)

            except Exception as e:
                logger.error(f"Error analyzing Git repository: {e}")
                return {"error": str(e), "conventions": [], "languages": [], "file_count": 0}

    def _analyze_local_dir(
        self, dir_path: str | Path, original_url: str | None = None
    ) -> dict[str, Any]:
        """Analyze a local directory."""
        dir_path_obj = Path(dir_path) if isinstance(dir_path, str) else dir_path
        if not dir_path_obj.exists():
            return {
                "error": f"Directory does not exist: {dir_path_obj}",
                "conventions": [],
                "languages": [],
                "file_count": 0,
            }

        # Find source files
        source_files = self._find_source_files(dir_path_obj)
        if not source_files:
            return {
                "error": "No source files found",
                "conventions": [],
                "languages": [],
                "file_count": 0,
            }

        # Analyze files
        conventions = []
        detected_languages: set[ProgrammingLanguage] = set()

        for file_path in source_files[: self.max_files]:
            lang = self._detect_language(file_path)
            if lang == ProgrammingLanguage.UNKNOWN:
                continue

            detected_languages.add(lang)
            file_conventions = self._analyze_file(file_path, lang)
            conventions.extend(file_conventions)

        # Deduplicate and score conventions
        unique_conventions = self._deduplicate_conventions(conventions)

        # Store conventions
        convention_ids = []
        for conv in unique_conventions:
            if original_url:
                conv.source_repository = original_url

            # Check if similar convention already exists
            existing = self._find_similar_convention(conv)
            if existing:
                # Update confidence
                updates = {
                    "confidence": min(1.0, existing.confidence + 0.1),
                    "source_repository": original_url or existing.source_repository,
                }
                self.storage.update_convention(existing.id, updates)
                convention_ids.append(existing.id)
            else:
                self.storage.save_convention(conv)
                convention_ids.append(conv.id)

        # Calculate compliance score (simplified)
        compliance_score = self._calculate_compliance_score(unique_conventions)

        # Group conventions by category
        category_summary: dict[ConventionCategory, int] = {}
        for conv in unique_conventions:
            category_summary[conv.category] = category_summary.get(conv.category, 0) + 1

        return {
            "conventions": unique_conventions,
            "convention_ids": convention_ids,
            "languages": list(detected_languages),
            "file_count": len(source_files[: self.max_files]),
            "compliance_score": compliance_score,
            "category_summary": category_summary,
            "error": None,
        }

    def _find_source_files(self, dir_path: Path) -> list[Path]:
        """Find source code files in directory."""
        source_files = []

        # Define file extensions for each language
        extensions = {
            ProgrammingLanguage.PYTHON: [".py"],
            ProgrammingLanguage.JAVASCRIPT: [".js", ".jsx"],
            ProgrammingLanguage.TYPESCRIPT: [".ts", ".tsx"],
            ProgrammingLanguage.JAVA: [".java"],
            ProgrammingLanguage.GO: [".go"],
            ProgrammingLanguage.RUST: [".rs"],
            ProgrammingLanguage.CPP: [".cpp", ".cc", ".cxx", ".h", ".hpp"],
            ProgrammingLanguage.CSHARP: [".cs"],
            ProgrammingLanguage.RUBY: [".rb"],
            ProgrammingLanguage.PHP: [".php"],
            ProgrammingLanguage.SWIFT: [".swift"],
            ProgrammingLanguage.KOTLIN: [".kt", ".kts"],
            ProgrammingLanguage.SCALA: [".scala"],
            ProgrammingLanguage.DART: [".dart"],
        }

        # Collect all extensions
        all_extensions = []
        for exts in extensions.values():
            all_extensions.extend(exts)

        # Walk directory
        for ext in all_extensions:
            for file in dir_path.rglob(f"*{ext}"):
                if file.is_file():
                    source_files.append(file)

        return source_files

    def _detect_language(self, file_path: Path) -> ProgrammingLanguage:
        """Detect programming language from file extension."""
        ext = file_path.suffix.lower()

        language_map = {
            ".py": ProgrammingLanguage.PYTHON,
            ".js": ProgrammingLanguage.JAVASCRIPT,
            ".jsx": ProgrammingLanguage.JAVASCRIPT,
            ".ts": ProgrammingLanguage.TYPESCRIPT,
            ".tsx": ProgrammingLanguage.TYPESCRIPT,
            ".java": ProgrammingLanguage.JAVA,
            ".go": ProgrammingLanguage.GO,
            ".rs": ProgrammingLanguage.RUST,
            ".cpp": ProgrammingLanguage.CPP,
            ".cc": ProgrammingLanguage.CPP,
            ".cxx": ProgrammingLanguage.CPP,
            ".h": ProgrammingLanguage.CPP,
            ".hpp": ProgrammingLanguage.CPP,
            ".cs": ProgrammingLanguage.CSHARP,
            ".rb": ProgrammingLanguage.RUBY,
            ".php": ProgrammingLanguage.PHP,
            ".swift": ProgrammingLanguage.SWIFT,
            ".kt": ProgrammingLanguage.KOTLIN,
            ".kts": ProgrammingLanguage.KOTLIN,
            ".scala": ProgrammingLanguage.SCALA,
            ".dart": ProgrammingLanguage.DART,
        }

        return language_map.get(ext, ProgrammingLanguage.UNKNOWN)

    def _analyze_file(self, file_path: Path, language: ProgrammingLanguage) -> list[CodeConvention]:
        """Analyze a single file for coding conventions."""
        conventions = []

        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")

            if language == ProgrammingLanguage.PYTHON:
                conventions.extend(self._analyze_python(content, str(file_path)))
            elif language in [ProgrammingLanguage.JAVASCRIPT, ProgrammingLanguage.TYPESCRIPT]:
                conventions.extend(self._analyze_javascript(content, str(file_path)))
            elif language == ProgrammingLanguage.JAVA:
                conventions.extend(self._analyze_java(content, str(file_path)))
            elif language == ProgrammingLanguage.GO:
                conventions.extend(self._analyze_go(content, str(file_path)))
            # Add more language analyzers as needed

        except Exception as e:
            logger.warning(f"Error analyzing file {file_path}: {e}")

        return conventions

    def _analyze_python(self, content: str, file_path: str) -> list[CodeConvention]:
        """Analyze Python code for conventions."""
        conventions = []

        try:
            tree = ast.parse(content)

            # Check for docstrings
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Module)):
                    if ast.get_docstring(node) is not None:
                        conventions.append(
                            self._create_convention(
                                language=ProgrammingLanguage.PYTHON,
                                category=ConventionCategory.DOCUMENTATION,
                                description="Functions and classes should have docstrings",
                                pattern=r'""".*?"""|\'\'\'.*?\'\'\'',
                                severity=ConventionSeverity.RECOMMENDED,
                                source_repository=file_path,
                            )
                        )

            # Check for type hints
            type_hint_pattern = r"def \w+\([^)]*\) -> [^:]+:"
            if re.search(type_hint_pattern, content):
                conventions.append(
                    self._create_convention(
                        language=ProgrammingLanguage.PYTHON,
                        category=ConventionCategory.DOCUMENTATION,
                        description="Functions should have return type hints",
                        pattern=type_hint_pattern,
                        severity=ConventionSeverity.RECOMMENDED,
                        source_repository=file_path,
                    )
                )

            # Check for snake_case naming
            snake_case_pattern = r"def ([a-z][a-z0-9_]*)\("
            for match in re.finditer(snake_case_pattern, content):
                conventions.append(
                    self._create_convention(
                        language=ProgrammingLanguage.PYTHON,
                        category=ConventionCategory.NAMING,
                        description="Function names should use snake_case",
                        pattern=snake_case_pattern,
                        severity=ConventionSeverity.REQUIRED,
                        source_repository=file_path,
                    )
                )
                break  # Only need one example

            # Check for imports at top
            import_lines = [
                i
                for i, line in enumerate(content.split("\n"))
                if line.strip().startswith(("import ", "from "))
            ]
            if import_lines:
                first_non_import = next(
                    (
                        i
                        for i, line in enumerate(content.split("\n"))
                        if line.strip()
                        and not line.strip().startswith(("import ", "from ", "#", '"""'))
                    ),
                    0,
                )
                if first_non_import > 0 and any(i > first_non_import for i in import_lines):
                    conventions.append(
                        self._create_convention(
                            language=ProgrammingLanguage.PYTHON,
                            category=ConventionCategory.STRUCTURE,
                            description="Imports should be at the top of the file",
                            severity=ConventionSeverity.REQUIRED,
                            source_repository=file_path,
                        )
                    )

        except SyntaxError:
            # Skip files with syntax errors
            pass

        return conventions

    def _analyze_javascript(self, content: str, file_path: str) -> list[CodeConvention]:
        """Analyze JavaScript/TypeScript code for conventions."""
        conventions = []

        # Check for const/let vs var
        if "var " in content and ("const " in content or "let " in content):
            conventions.append(
                self._create_convention(
                    language=ProgrammingLanguage.JAVASCRIPT,
                    category=ConventionCategory.STRUCTURE,
                    description="Use const/let instead of var",
                    pattern=r"\bvar\b",
                    severity=ConventionSeverity.RECOMMENDED,
                    source_repository=file_path,
                )
            )

        # Check for === vs ==
        if " == " in content and " === " in content:
            conventions.append(
                self._create_convention(
                    language=ProgrammingLanguage.JAVASCRIPT,
                    category=ConventionCategory.STRUCTURE,
                    description="Use strict equality (===) instead of loose equality (==)",
                    pattern=r"==",
                    severity=ConventionSeverity.RECOMMENDED,
                    source_repository=file_path,
                )
            )

        # Check for camelCase naming
        camel_case_pattern = r"function ([a-z][a-zA-Z0-9]*)\("
        for match in re.finditer(camel_case_pattern, content):
            conventions.append(
                self._create_convention(
                    language=ProgrammingLanguage.JAVASCRIPT,
                    category=ConventionCategory.NAMING,
                    description="Function names should use camelCase",
                    pattern=camel_case_pattern,
                    severity=ConventionSeverity.REQUIRED,
                    source_repository=file_path,
                )
            )
            break

        # Check for semicolons
        lines = content.split("\n")
        semicolon_lines = sum(1 for line in lines if line.strip().endswith(";"))
        no_semicolon_lines = sum(
            1
            for line in lines
            if line.strip()
            and not line.strip().endswith(";")
            and not line.strip().startswith(("//", "/*", "*", "#"))
        )

        if semicolon_lines > 0 and no_semicolon_lines > 0:
            conventions.append(
                self._create_convention(
                    language=ProgrammingLanguage.JAVASCRIPT,
                    category=ConventionCategory.FORMATTING,
                    description="Be consistent with semicolon usage",
                    severity=ConventionSeverity.RECOMMENDED,
                    source_repository=file_path,
                )
            )

        return conventions

    def _analyze_java(self, content: str, file_path: str) -> list[CodeConvention]:
        """Analyze Java code for conventions."""
        conventions = []

        # Check for CamelCase class names
        class_pattern = r"class ([A-Z][a-zA-Z0-9]*)"
        for match in re.finditer(class_pattern, content):
            conventions.append(
                self._create_convention(
                    language=ProgrammingLanguage.JAVA,
                    category=ConventionCategory.NAMING,
                    description="Class names should use PascalCase",
                    pattern=class_pattern,
                    severity=ConventionSeverity.REQUIRED,
                    source_repository=file_path,
                )
            )
            break

        # Check for Javadoc comments
        if "/**" in content:
            conventions.append(
                self._create_convention(
                    language=ProgrammingLanguage.JAVA,
                    category=ConventionCategory.DOCUMENTATION,
                    description="Public methods should have Javadoc comments",
                    pattern=r"/\*\*.*?\*/",
                    severity=ConventionSeverity.RECOMMENDED,
                    source_repository=file_path,
                )
            )

        return conventions

    def _analyze_go(self, content: str, file_path: str) -> list[CodeConvention]:
        """Analyze Go code for conventions."""
        conventions = []

        # Check for error handling
        if "err != nil" in content or "err == nil" in content:
            conventions.append(
                self._create_convention(
                    language=ProgrammingLanguage.GO,
                    category=ConventionCategory.ERROR_HANDLING,
                    description="Check errors explicitly",
                    severity=ConventionSeverity.REQUIRED,
                    source_repository=file_path,
                )
            )

        # Check for camelCase naming
        func_pattern = r"func ([a-z][a-zA-Z0-9]*)"
        for match in re.finditer(func_pattern, content):
            conventions.append(
                self._create_convention(
                    language=ProgrammingLanguage.GO,
                    category=ConventionCategory.NAMING,
                    description="Function names should use camelCase",
                    pattern=func_pattern,
                    severity=ConventionSeverity.REQUIRED,
                    source_repository=file_path,
                )
            )
            break

        return conventions

    def _create_convention(
        self,
        language: ProgrammingLanguage,
        category: ConventionCategory,
        description: str,
        pattern: str | None = None,
        severity: ConventionSeverity = ConventionSeverity.RECOMMENDED,
        source_repository: str | None = None,
    ) -> CodeConvention:
        """Create a CodeConvention object with generated ID."""
        import hashlib

        # Generate ID from language, category, and description
        id_base = f"{language.value}_{category.value}_{description}"
        convention_id = hashlib.md5(id_base.encode()).hexdigest()[:16]

        return CodeConvention(
            id=convention_id,
            language=language,
            category=category,
            severity=severity,
            pattern=pattern,
            description=description,
            source_repository=source_repository,
            confidence=0.7,  # Initial confidence
        )

    def _deduplicate_conventions(self, conventions: list[CodeConvention]) -> list[CodeConvention]:
        """Deduplicate conventions based on ID."""
        seen = set()
        unique = []

        for conv in conventions:
            if conv.id not in seen:
                seen.add(conv.id)
                unique.append(conv)

        return unique

    def _find_similar_convention(self, convention: CodeConvention) -> CodeConvention | None:
        """Find a similar convention in storage."""
        # Look for conventions with same language and similar description
        existing = self.storage.get_conventions(language=convention.language)

        for existing_conv in existing:
            # Simple similarity check: same language and category
            if (
                existing_conv.language == convention.language
                and existing_conv.category == convention.category
                and existing_conv.description.lower() == convention.description.lower()
            ):
                return existing_conv

        return None

    def _calculate_compliance_score(self, conventions: list[CodeConvention]) -> float:
        """Calculate a compliance score based on convention severity."""
        if not conventions:
            return 0.0

        severity_weights = {
            ConventionSeverity.REQUIRED: 1.0,
            ConventionSeverity.RECOMMENDED: 0.7,
            ConventionSeverity.SUGGESTED: 0.4,
            ConventionSeverity.OPTIONAL: 0.1,
        }

        total_weight = sum(severity_weights.get(conv.severity, 0.5) for conv in conventions)
        max_weight = len(conventions) * 1.0

        return total_weight / max_weight if max_weight > 0 else 0.0
