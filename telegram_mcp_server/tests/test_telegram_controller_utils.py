"""Tests for telegram_controller utility modules."""

import tempfile
import pytest
import os
import signal
from pathlib import Path
from unittest.mock import patch, MagicMock

from telegram_controller.pid_manager import PIDManager
from telegram_controller.port_allocator import PortAllocator
from telegram_controller.project_detector import detect_project_name


class TestPIDManager:
    """Tests for PIDManager class."""

    @pytest.fixture
    def temp_pids_dir(self):
        """Create temporary directory for PID files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_write_pid(self, temp_pids_dir):
        """Test writing PID file."""
        manager = PIDManager(temp_pids_dir)
        manager.write_pid("test_instance", 12345)
        
        pid_file = temp_pids_dir / "test_instance.pid"
        assert pid_file.exists()
        assert pid_file.read_text() == "12345"

    def test_read_pid(self, temp_pids_dir):
        """Test reading PID file."""
        manager = PIDManager(temp_pids_dir)
        manager.write_pid("test_instance", 12345)
        
        pid = manager.read_pid("test_instance")
        assert pid == 12345

    def test_read_pid_nonexistent(self, temp_pids_dir):
        """Test reading non-existent PID file."""
        manager = PIDManager(temp_pids_dir)
        
        pid = manager.read_pid("nonexistent")
        assert pid is None

    def test_remove_pid(self, temp_pids_dir):
        """Test removing PID file."""
        manager = PIDManager(temp_pids_dir)
        manager.write_pid("test_instance", 12345)
        
        assert (temp_pids_dir / "test_instance.pid").exists()
        
        manager.remove_pid("test_instance")
        
        assert not (temp_pids_dir / "test_instance.pid").exists()

    def test_write_pid_error(self, temp_pids_dir):
        """Test error handling when writing PID fails."""
        manager = PIDManager(temp_pids_dir)
        
        # Make directory read-only to cause write error
        os.chmod(temp_pids_dir, 0o444)
        
        try:
            # Should not raise exception, just log error
            manager.write_pid("test_instance", 12345)
        except Exception:
            pytest.fail("Should not raise exception on write failure")
        finally:
            # Restore permissions for cleanup
            os.chmod(temp_pids_dir, 0o755)

    @patch('telegram_controller.pid_manager.os.kill')
    def test_is_process_running(self, mock_kill):
        """Test checking if process is running."""
        # Process is running
        mock_kill.return_value = None
        assert PIDManager.is_process_running(12345) is True

    @patch('telegram_controller.pid_manager.os.kill')
    def test_is_process_not_running(self, mock_kill):
        """Test checking if process is not running."""
        # Process is not running
        mock_kill.side_effect = OSError()
        assert PIDManager.is_process_running(12345) is False

    @patch('telegram_controller.pid_manager.os.kill')
    @patch('telegram_controller.pid_manager.time.sleep')
    def test_cleanup_orphans(self, mock_sleep, mock_kill, temp_pids_dir):
        """Test cleaning up orphan processes."""
        manager = PIDManager(temp_pids_dir)
        
        # Create mock PID files
        manager.write_pid("zombie", 22222)
        manager.write_pid("dead", 33333)
        
        # Mock is_process_running - zombie is still running, dead is already dead
        call_count = {'zombie': 0}
        
        def is_running_side_effect(pid):
            if pid == 22222:
                call_count['zombie'] += 1
                # First call: zombie is running
                # Subsequent calls: after kill, it's not running
                return call_count['zombie'] == 1
            return False  # Dead process is not running
        
        with patch.object(PIDManager, 'is_process_running', side_effect=is_running_side_effect):
            # Run cleanup - should kill zombie process (which is running) and clean up dead process PID file
            cleaned = manager.cleanup_orphans(set())  # No managed processes
            
            # Should clean up 1 orphan (zombie process that was running and was killed)
            assert cleaned == 1
            # Verify zombie process was killed
            assert mock_kill.called
            
        # Verify both PID files were removed
        assert not (temp_pids_dir / "zombie.pid").exists()
        assert not (temp_pids_dir / "dead.pid").exists()

    def test_cleanup_orphans_empty_dir(self, temp_pids_dir):
        """Test cleanup with no PID files."""
        manager = PIDManager(temp_pids_dir)
        cleaned = manager.cleanup_orphans(set())
        assert cleaned == 0


class TestPortAllocator:
    """Tests for PortAllocator class."""

    def test_init_default_range(self):
        """Test initialization with default port range."""
        allocator = PortAllocator()
        assert allocator.port_range_start == 4097
        assert allocator.port_range_end == 4200
        assert len(allocator.used_ports) == 0

    def test_init_custom_range(self):
        """Test initialization with custom port range."""
        allocator = PortAllocator(5000, 5010)
        assert allocator.port_range_start == 5000
        assert allocator.port_range_end == 5010

    def test_is_port_available(self):
        """Test checking if port is available."""
        # Most high ports should be available
        allocator = PortAllocator()
        assert allocator.is_port_available(54321) is True

    def test_allocate(self):
        """Test allocating a port."""
        allocator = PortAllocator(50000, 50100)
        port = allocator.allocate()
        
        assert port in range(50000, 50100)
        assert port in allocator.used_ports

    def test_allocate_no_ports_available(self):
        """Test allocating when no ports are available."""
        allocator = PortAllocator(5000, 5001)  # Only port 5000
        
        # Mock is_port_available to return False
        with patch.object(allocator, 'is_port_available', return_value=False):
            with pytest.raises(RuntimeError) as exc_info:
                allocator.allocate()
            
            assert "No available ports" in str(exc_info.value)

    def test_allocate_specific_available(self):
        """Test allocating a specific port that's available."""
        allocator = PortAllocator(5000, 5100)
        
        with patch.object(allocator, 'is_port_available', return_value=True):
            port = allocator.allocate_specific(5000)
            assert port == 5000
            assert 5000 in allocator.used_ports

    def test_allocate_specific_unavailable(self):
        """Test allocating specific port when unavailable (allocates new)."""
        allocator = PortAllocator(5000, 5100)
        
        # Port 5000 is not available, should allocate new one
        # First call is for port 5000 in allocate_specific, second is for port 5000 in allocate(), third for port 5001
        with patch.object(allocator, 'is_port_available', side_effect=[False, False, True]):
            port = allocator.allocate_specific(5000)
            # Should return a different port since 5000 is not available
            assert port != 5000
            assert port == 5001

    def test_release(self):
        """Test releasing a port."""
        allocator = PortAllocator()
        allocator.used_ports = {5000, 5001}
        
        allocator.release(5000)
        
        assert 5000 not in allocator.used_ports
        assert 5001 in allocator.used_ports

    def test_release_not_allocated(self):
        """Test releasing a port that wasn't allocated."""
        allocator = PortAllocator()
        allocator.used_ports = {5000}
        
        # Should not raise exception
        allocator.release(5001)
        
        assert 5000 in allocator.used_ports

    def test_mark_used(self):
        """Test marking a port as used."""
        allocator = PortAllocator()
        allocator.mark_used(5000)
        
        assert 5000 in allocator.used_ports

    def test_allocate_marks_as_used(self):
        """Test that allocate marks port as used."""
        allocator = PortAllocator(50000, 50005)
        
        with patch.object(allocator, 'is_port_available', return_value=True):
            port = allocator.allocate()
            assert port in allocator.used_ports


class TestProjectDetector:
    """Tests for project name detection."""

    def test_detect_from_git(self, tmp_path):
        """Test detecting project name from git repository."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        
        config_file = git_dir / "config"
        config_file.write_text("""
[core]
    repositoryformatversion = 0
[remote "origin"]
    url = https://github.com/user/my-awesome-project.git
""")
        
        project_name = detect_project_name(tmp_path)
        assert project_name == "my-awesome-project"

    def test_detect_from_git_ssh(self, tmp_path):
        """Test detecting project name from git SSH URL."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        
        config_file = git_dir / "config"
        config_file.write_text("""
[remote "origin"]
    url = git@github.com:user/my-project.git
""")
        
        project_name = detect_project_name(tmp_path)
        assert project_name == "my-project"

    def test_detect_from_package_json(self, tmp_path):
        """Test detecting project name from package.json."""
        package_json = tmp_path / "package.json"
        package_json.write_text('{"name": "my-package"}')
        
        project_name = detect_project_name(tmp_path)
        assert project_name == "my-package"

    def test_detect_from_scoped_package_json(self, tmp_path):
        """Test detecting scoped package name from package.json."""
        package_json = tmp_path / "package.json"
        package_json.write_text('{"name": "@org/my-package"}')
        
        project_name = detect_project_name(tmp_path)
        assert project_name == "my-package"

    def test_detect_from_pyproject(self, tmp_path):
        """Test detecting project name from pyproject.toml."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""
[project]
name = "my-python-project"
version = "1.0.0"
""")
        
        project_name = detect_project_name(tmp_path)
        assert project_name == "my-python-project"

    def test_detect_from_pyproject_poetry(self, tmp_path):
        """Test detecting project name from Poetry pyproject.toml."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""
[tool.poetry]
name = "my-poetry-project"
""")
        
        project_name = detect_project_name(tmp_path)
        assert project_name == "my-poetry-project"

    def test_detect_from_go_mod(self, tmp_path):
        """Test detecting project name from go.mod."""
        go_mod = tmp_path / "go.mod"
        go_mod.write_text("module github.com/user/my-go-project\n\ngo 1.19")
        
        project_name = detect_project_name(tmp_path)
        assert project_name == "my-go-project"

    def test_detect_from_cargo_toml(self, tmp_path):
        """Test detecting project name from Cargo.toml."""
        cargo_toml = tmp_path / "Cargo.toml"
        cargo_toml.write_text("""
[package]
name = "my-rust-project"
version = "0.1.0"
""")
        
        project_name = detect_project_name(tmp_path)
        assert project_name == "my-rust-project"

    def test_detect_fallback_to_directory_name(self, tmp_path):
        """Test falling back to directory name when no config found."""
        # Ensure no config files exist
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        
        project_name = detect_project_name(project_dir)
        assert project_name == "my-project"

    def test_detect_git_url_without_dot_git(self, tmp_path):
        """Test handling git URLs without .git extension."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        
        config_file = git_dir / "config"
        config_file.write_text("""
[remote "origin"]
    url = https://github.com/user/my-project
""")
        
        project_name = detect_project_name(tmp_path)
        assert project_name == "my-project"

    def test_detect_priority_git_over_package_json(self, tmp_path):
        """Test that git config takes priority over package.json."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        
        config_file = git_dir / "config"
        config_file.write_text("""
[remote "origin"]
    url = https://github.com/user/git-project.git
""")
        
        package_json = tmp_path / "package.json"
        package_json.write_text('{"name": "npm-project"}')
        
        project_name = detect_project_name(tmp_path)
        # Git should take priority
        assert project_name == "git-project"

    def test_detect_malformed_json(self, tmp_path):
        """Test handling malformed package.json."""
        package_json = tmp_path / "package.json"
        package_json.write_text('{"name": invalid}')
        
        # Should fall back to directory name
        project_name = detect_project_name(tmp_path)
        assert project_name == tmp_path.name

    def test_detect_no_project_files(self, tmp_path):
        """Test when no project detection files exist."""
        project_name = detect_project_name(tmp_path)
        assert project_name == tmp_path.name