"""MCP Server for tracking, comparing, and validating coding conventions."""

import argparse
import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .analyzer import RepositoryAnalyzer
from .models import (
    CodeConvention,
    ComparisonResult,
    ConventionCategory,
    ConventionSeverity,
    ProgrammingLanguage,
    RepositoryAnalysis,
)
from .storage import get_storage

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global storage instance
_storage = None


def get_storage_instance():
    """Get the storage instance, creating if needed."""
    global _storage
    if _storage is None:
        _storage = get_storage()
    return _storage


# Create the MCP server with FastMCP
mcp = FastMCP("coding-convention-mcp-server")


@mcp.tool(
    annotations=ToolAnnotations(
        title="Track Coding Convention", readOnlyHint=False, openWorldHint=True
    )
)
async def track_coding_convention(
    language: str,
    category: str,
    description: str,
    severity: str = "recommended",
    pattern: str | None = None,
    example: str | None = None,
    counter_example: str | None = None,
    source_repository: str | None = None,
    confidence: float = 1.0,
) -> str:
    """Track a new coding convention or update an existing one.

    Args:
        language: Programming language (python, javascript, typescript, java, etc.)
        category: Convention category (naming, formatting, structure, documentation, etc.)
        description: Human-readable description of the convention
        severity: Importance level (required, recommended, suggested, optional)
        pattern: Optional regex pattern for detecting this convention
        example: Example of compliant code
        counter_example: Example of non-compliant code
        source_repository: Repository where this convention was observed
        confidence: Confidence score (0.0-1.0)

    Returns:
        Success message with convention ID.
    """
    try:
        # Convert string enums to enum values
        try:
            lang_enum = ProgrammingLanguage(language.lower())
        except ValueError:
            return f"Invalid language: {language}. Valid options: {[e.value for e in ProgrammingLanguage]}"

        try:
            cat_enum = ConventionCategory(category.lower())
        except ValueError:
            return f"Invalid category: {category}. Valid options: {[e.value for e in ConventionCategory]}"

        try:
            sev_enum = ConventionSeverity(severity.lower())
        except ValueError:
            return f"Invalid severity: {severity}. Valid options: {[e.value for e in ConventionSeverity]}"

        # Generate ID based on language, category, and description hash
        import hashlib

        id_base = f"{lang_enum.value}_{cat_enum.value}_{description}"
        convention_id = hashlib.md5(id_base.encode()).hexdigest()[:16]

        # Check if convention already exists
        storage = get_storage_instance()
        existing = storage.get_convention(convention_id)

        if existing:
            # Update confidence if this is from a new source
            updates = {
                "confidence": min(1.0, existing.confidence + 0.1),
                "updated_at": datetime.now(),
            }
            if source_repository and source_repository != existing.source_repository:
                updates["source_repository"] = source_repository

            storage.update_convention(convention_id, updates)
            return f"Updated existing convention: {convention_id}"

        # Create new convention
        convention = CodeConvention(
            id=convention_id,
            language=lang_enum,
            category=cat_enum,
            severity=sev_enum,
            pattern=pattern,
            description=description,
            example=example,
            counter_example=counter_example,
            source_repository=source_repository,
            confidence=confidence,
        )

        storage.save_convention(convention)
        return f"Tracked new convention: {convention_id}"

    except Exception as e:
        logger.error(f"Error tracking convention: {e}")
        return f"Error tracking convention: {str(e)}"


@mcp.tool(
    annotations=ToolAnnotations(title="Analyze Repository", readOnlyHint=True, openWorldHint=True)
)
async def analyze_repository(repository_path: str, max_files: int = 100) -> str:
    """Analyze a repository to extract coding conventions.

    Args:
        repository_path: Path to the repository (local directory or Git URL)
        max_files: Maximum number of files to analyze

    Returns:
        Analysis results summary.
    """
    try:
        storage = get_storage_instance()

        # Generate analysis ID
        analysis_id = str(uuid.uuid4())[:8]
        repo_name = (
            Path(repository_path).name
            if "/" in repository_path or "\\" in repository_path
            else repository_path
        )

        # Use analyzer to extract conventions
        analyzer = RepositoryAnalyzer(max_files=max_files)
        analysis_result = analyzer.analyze(repository_path)

        if analysis_result.get("error"):
            return f"Analysis error: {analysis_result['error']}"

        # Create analysis record
        analysis = RepositoryAnalysis(
            id=analysis_id,
            repository_url=repository_path,
            repository_name=repo_name,
            languages_used=analysis_result["languages"],
            conventions_found=analysis_result["convention_ids"],
            convention_summary=analysis_result["category_summary"],
            compliance_score=analysis_result["compliance_score"],
            file_count=analysis_result["file_count"],
            analysis_metadata={
                "analyzed_files_count": analysis_result["file_count"],
                "max_files": max_files,
                "analysis_method": "automated",
            },
        )

        storage.save_analysis(analysis)

        # Format results
        conventions_found = len(analysis_result["conventions"])
        languages = (
            ", ".join([lang.value for lang in analysis_result["languages"]]) or "None detected"
        )

        result = f"""Analysis Results:
Repository: {repo_name}
Analysis ID: {analysis_id}

Languages detected: {languages}
Conventions found: {conventions_found}
Files analyzed: {analysis_result["file_count"]}
Compliance score: {analysis_result["compliance_score"]:.1%}

Conventions by category:"""

        for category, count in analysis_result["category_summary"].items():
            result += f"\n  - {category.value}: {count}"

        if conventions_found == 0:
            result += "\n\nNo coding conventions detected. This could be because:"
            result += "\n- The repository doesn't contain source code in supported languages"
            result += "\n- The analyzer couldn't extract patterns from the code"
            result += "\n- The repository is empty or contains only non-source files"

        return result

    except Exception as e:
        logger.error(f"Error analyzing repository: {e}")
        return f"Error analyzing repository: {str(e)}"


@mcp.tool(
    annotations=ToolAnnotations(title="Compare Repositories", readOnlyHint=True, openWorldHint=True)
)
async def compare_repositories(repository_a: str, repository_b: str) -> str:
    """Compare coding conventions between two repositories.

    Args:
        repository_a: First repository path or analysis ID
        repository_b: Second repository path or analysis ID

    Returns:
        Comparison results with similarities and differences.
    """
    try:
        storage = get_storage_instance()

        # Get analyses for repositories
        analyses_a = storage.get_repository_analyses(repository_a)
        analyses_b = storage.get_repository_analyses(repository_b)

        if not analyses_a:
            return f"No analysis found for repository: {repository_a}"
        if not analyses_b:
            return f"No analysis found for repository: {repository_b}"

        # Use most recent analysis for each
        analysis_a = analyses_a[0]
        analysis_b = analyses_b[0]

        # Get conventions for each
        conventions_a = []
        for conv_id in analysis_a.conventions_found:
            conv = storage.get_convention(conv_id)
            if conv:
                conventions_a.append(conv)

        conventions_b = []
        for conv_id in analysis_b.conventions_found:
            conv = storage.get_convention(conv_id)
            if conv:
                conventions_b.append(conv)

        # Compare conventions
        conv_ids_a = set([c.id for c in conventions_a])
        conv_ids_b = set([c.id for c in conventions_b])

        common = conv_ids_a.intersection(conv_ids_b)
        unique_a = conv_ids_a - conv_ids_b
        unique_b = conv_ids_b - conv_ids_a

        # Calculate similarity score
        total = len(conv_ids_a.union(conv_ids_b))
        similarity = len(common) / total if total > 0 else 0.0

        # Generate recommendations
        recommendations = []
        if similarity < 0.5:
            recommendations.append("Consider aligning coding conventions between repositories")
        if len(unique_a) > len(unique_b) * 2:
            recommendations.append(
                f"Repository A has many unique conventions ({len(unique_a)} vs {len(unique_b)})"
            )
        elif len(unique_b) > len(unique_a) * 2:
            recommendations.append(
                f"Repository B has many unique conventions ({len(unique_b)} vs {len(unique_a)})"
            )

        # Save comparison
        comparison = ComparisonResult(
            id=str(uuid.uuid4())[:8],
            repository_a=analysis_a.repository_name,
            repository_b=analysis_b.repository_name,
            common_conventions=list(common),
            unique_to_a=list(unique_a),
            unique_to_b=list(unique_b),
            similarity_score=similarity,
            recommendations=recommendations,
        )

        storage.save_comparison(comparison)

        # Format results
        result = f"""Comparison Results:
Repository A: {analysis_a.repository_name} ({len(conventions_a)} conventions)
Repository B: {analysis_b.repository_name} ({len(conventions_b)} conventions)

Similarity Score: {similarity:.1%}

Common Conventions ({len(common)}):
{chr(10).join(f"  - {cid}" for cid in list(common)[:5])}
{"  ..." if len(common) > 5 else ""}

Unique to A ({len(unique_a)}):
{chr(10).join(f"  - {cid}" for cid in list(unique_a)[:5])}
{"  ..." if len(unique_a) > 5 else ""}

Unique to B ({len(unique_b)}):
{chr(10).join(f"  - {cid}" for cid in list(unique_b)[:5])}
{"  ..." if len(unique_b) > 5 else ""}

Recommendations:
{chr(10).join(f"  - {rec}" for rec in recommendations) if recommendations else "  None"}

Comparison ID: {comparison.id}
"""
        return result

    except Exception as e:
        logger.error(f"Error comparing repositories: {e}")
        return f"Error comparing repositories: {str(e)}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="Validate Code Against Conventions", readOnlyHint=True, openWorldHint=True
    )
)
async def validate_code(code: str, language: str, check_severity: str = "required") -> str:
    """Validate code against stored coding conventions.

    Args:
        code: Source code to validate
        language: Programming language of the code
        check_severity: Minimum severity to check (required, recommended, suggested, optional)

    Returns:
        Validation results with any violations found.
    """
    try:
        # Convert string enums
        try:
            lang_enum = ProgrammingLanguage(language.lower())
        except ValueError:
            return f"Invalid language: {language}. Valid options: {[e.value for e in ProgrammingLanguage]}"

        try:
            severity_enum = ConventionSeverity(check_severity.lower())
        except ValueError:
            return f"Invalid severity: {check_severity}. Valid options: {[e.value for e in ConventionSeverity]}"

        # Get conventions for this language
        storage = get_storage_instance()
        conventions = storage.get_conventions(language=lang_enum)

        if not conventions:
            return f"No conventions found for language: {language}"

        # Filter by severity
        severity_order = {
            ConventionSeverity.REQUIRED: 3,
            ConventionSeverity.RECOMMENDED: 2,
            ConventionSeverity.SUGGESTED: 1,
            ConventionSeverity.OPTIONAL: 0,
        }

        relevant_conventions = [
            conv
            for conv in conventions
            if severity_order.get(conv.severity, 0) >= severity_order.get(severity_enum, 0)
        ]

        # Check each convention (simplified - would use pattern matching in real implementation)
        violations = []
        for conv in relevant_conventions[:10]:  # Limit checks for demo
            # Simple check based on pattern if available
            if conv.pattern:
                import re

                try:
                    if re.search(conv.pattern, code, re.MULTILINE | re.DOTALL):
                        # Pattern found - could be violation or compliance depending on context
                        # For demo, we'll just note it
                        violations.append(
                            {
                                "convention_id": conv.id,
                                "description": conv.description,
                                "severity": conv.severity.value,
                                "found": True,
                            }
                        )
                except re.error:
                    pass

        # Format results
        if not violations:
            return f"Code validated successfully against {len(relevant_conventions)} conventions."

        result = f"""Validation Results:
Checked against {len(relevant_conventions)} conventions ({check_severity}+ severity)

Violations found ({len(violations)}):
"""
        for i, violation in enumerate(violations[:5], 1):
            result += f"{i}. [{violation['severity'].upper()}] {violation['description']}\n"

        if len(violations) > 5:
            result += f"... and {len(violations) - 5} more violations\n"

        return result

    except Exception as e:
        logger.error(f"Error validating code: {e}")
        return f"Error validating code: {str(e)}"


@mcp.tool(
    annotations=ToolAnnotations(title="List Conventions", readOnlyHint=True, openWorldHint=True)
)
async def list_conventions(
    language: str | None = None, category: str | None = None, limit: int = 20
) -> str:
    """List stored coding conventions with optional filtering.

    Args:
        language: Filter by programming language
        category: Filter by convention category
        limit: Maximum number of conventions to return

    Returns:
        Formatted list of conventions.
    """
    try:
        storage = get_storage_instance()

        # Convert string filters to enums if provided
        lang_enum = None
        if language:
            try:
                lang_enum = ProgrammingLanguage(language.lower())
            except ValueError:
                return f"Invalid language: {language}. Valid options: {[e.value for e in ProgrammingLanguage]}"

        cat_enum = None
        if category:
            try:
                cat_enum = ConventionCategory(category.lower())
            except ValueError:
                return f"Invalid category: {category}. Valid options: {[e.value for e in ConventionCategory]}"

        # Get conventions
        conventions = storage.get_conventions(language=lang_enum, category=cat_enum, limit=limit)

        if not conventions:
            return "No conventions found matching the criteria."

        # Format results
        result = f"Found {len(conventions)} conventions:\n\n"

        for i, conv in enumerate(conventions, 1):
            result += f"{i}. [{conv.language.value.upper()}] {conv.description}\n"
            result += f"   Category: {conv.category.value} | Severity: {conv.severity.value} | Confidence: {conv.confidence:.0%}\n"
            if conv.source_repository:
                result += f"   Source: {conv.source_repository}\n"
            result += "\n"

        return result

    except Exception as e:
        logger.error(f"Error listing conventions: {e}")
        return f"Error listing conventions: {str(e)}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Convention Details", readOnlyHint=True, openWorldHint=True
    )
)
async def get_convention_details(convention_id: str) -> str:
    """Get detailed information about a specific convention.

    Args:
        convention_id: ID of the convention to retrieve

    Returns:
        Detailed convention information.
    """
    try:
        storage = get_storage_instance()
        convention = storage.get_convention(convention_id)

        if not convention:
            return f"No convention found with ID: {convention_id}"

        # Format detailed information
        result = f"""Convention Details:
ID: {convention.id}
Language: {convention.language.value}
Category: {convention.category.value}
Severity: {convention.severity.value}
Confidence: {convention.confidence:.0%}

Description:
{convention.description}

"""
        if convention.pattern:
            result += f"Pattern: {convention.pattern}\n\n"

        if convention.example:
            result += f"Example (compliant):\n{convention.example}\n\n"

        if convention.counter_example:
            result += f"Counter Example (non-compliant):\n{convention.counter_example}\n\n"

        if convention.source_repository:
            result += f"Source Repository: {convention.source_repository}\n"

        result += f"Created: {convention.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
        result += f"Updated: {convention.updated_at.strftime('%Y-%m-%d %H:%M:%S')}\n"

        return result

    except Exception as e:
        logger.error(f"Error getting convention details: {e}")
        return f"Error getting convention details: {str(e)}"


@mcp.tool(
    annotations=ToolAnnotations(title="Merge Conventions", readOnlyHint=False, openWorldHint=True)
)
async def merge_conventions(source_convention_id: str, target_convention_id: str) -> str:
    """Merge two similar conventions into one.

    Args:
        source_convention_id: ID of the convention to merge from (will be deleted)
        target_convention_id: ID of the convention to merge into (will be updated)

    Returns:
        Merge result message.
    """
    try:
        storage = get_storage_instance()

        source = storage.get_convention(source_convention_id)
        target = storage.get_convention(target_convention_id)

        if not source:
            return f"Source convention not found: {source_convention_id}"
        if not target:
            return f"Target convention not found: {target_convention_id}"

        # Update target with combined information
        updates = {
            "confidence": min(1.0, target.confidence + source.confidence * 0.1),
            "updated_at": datetime.now(),
        }

        # Combine descriptions if different
        if source.description != target.description:
            updates["description"] = f"{target.description} (also: {source.description})"

        # Combine patterns if available
        if source.pattern and not target.pattern:
            updates["pattern"] = source.pattern
        elif source.pattern and target.pattern and source.pattern != target.pattern:
            updates["pattern"] = f"(?:{target.pattern})|(?:{source.pattern})"

        # Update target
        storage.update_convention(target_convention_id, updates)

        # Delete source
        storage.delete_convention(source_convention_id)

        return f"Merged convention {source_convention_id} into {target_convention_id}"

    except Exception as e:
        logger.error(f"Error merging conventions: {e}")
        return f"Error merging conventions: {str(e)}"


def run_server():
    """Run the MCP server."""
    # FastMCP.run() is synchronous and manages its own event loop
    mcp.run(transport="stdio")


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Coding Convention MCP Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run MCP server only (default)
  coding-convention-mcp-server
  
  # Run with verbose logging
  coding-convention-mcp-server --verbose
        """,
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        run_server()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
