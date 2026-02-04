"""QuantCode instance factory.

Factory for creating and managing QuantCode agent instances.
QuantCode is a financial news analysis agent that runs as an MCP server
with optional HTTP API support.
"""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import httpx

from .base import InstanceFactory
from ..instance import InstanceState, OpenCodeInstance

logger = logging.getLogger(__name__)


class QuantCodeInstanceFactory(InstanceFactory):
    """Factory for creating QuantCode instances.
    
    QuantCode is a financial news analysis agent that can run as:
    1. A standalone MCP server
    2. An HTTP API server
    3. A subprocess managed by the controller
    
    This factory manages spawning QuantCode processes for Telegram integration.
    """
    
    @property
    def instance_type(self) -> str:
        return "quantcode"
    
    @property
    def command_prefix(self) -> str:
        return "quantcode"
    
    @property
    def default_config(self) -> Dict[str, Any]:
        return {
            "server_path": "python",
            "module": "packages.news_quant_mcp.server",
            "http_enabled": True,
            "http_port_offset": 1000,  # Offset from main port for HTTP
        }
    
    async def create(
        self,
        instance_id: str,
        directory: Path,
        port: int,
        config: Dict[str, Any],
    ) -> OpenCodeInstance:
        """Create and start a QuantCode instance.
        
        Args:
            instance_id: Unique instance identifier
            directory: Working directory (should be quantcode project root)
            port: HTTP API port
            config: Instance configuration
            
        Returns:
            Created instance
        """
        # Merge with defaults
        merged_config = {**self.default_config, **config}
        
        # Build spawn command
        cmd = self.get_spawn_command_list(port, merged_config, directory)
        
        logger.info(f"Spawning QuantCode: {' '.join(cmd)} in {directory}")
        
        # Prepare environment
        env = os.environ.copy()
        env.update(self.get_env_vars(merged_config))
        
        # Set HTTP port environment variable
        env["QUANTCODE_HTTP_PORT"] = str(port)
        
        # Spawn process
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=directory,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        # Create instance object
        # Note: We reuse OpenCodeInstance for now, but this could be
        # a specialized QuantCodeInstance in the future
        instance = OpenCodeInstance(
            id=instance_id,
            directory=directory,
            port=port,
            state=InstanceState.STARTING,
            pid=process.pid,
            process=process,
            started_at=datetime.now(),
            provider_id="quantcode",
            model_id="news-quant-mcp",
            name=merged_config.get("name", "QuantCode"),
        )
        
        logger.info(f"Created QuantCode instance {instance.short_id} on port {port}")
        return instance
    
    async def health_check(self, instance: OpenCodeInstance) -> bool:
        """Check QuantCode health via HTTP API.
        
        Args:
            instance: Instance to check
            
        Returns:
            True if healthy
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{instance.url}/health")
                if resp.status_code == 200:
                    instance.last_health_check = datetime.now()
                    instance.health_check_failures = 0
                    if instance.state == InstanceState.STARTING:
                        instance.state = InstanceState.RUNNING
                    return True
        except Exception as e:
            logger.debug(f"Health check failed for {instance.short_id}: {e}")
            instance.health_check_failures += 1
        
        return False
    
    def get_spawn_command_list(
        self,
        port: int,
        config: Dict[str, Any],
        directory: Path
    ) -> List[str]:
        """Get the command to spawn QuantCode.
        
        Args:
            port: HTTP API port
            config: Instance configuration
            directory: Working directory
            
        Returns:
            Command as list of strings
        """
        server_path = config.get("server_path", "python")
        module = config.get("module", "packages.news_quant_mcp.server")
        
        # Check if there's a custom server script
        custom_script = config.get("script_path")
        if custom_script:
            return [server_path, custom_script, "--http-port", str(port)]
        
        # Default: run as Python module
        return [
            server_path,
            "-m", module,
        ]
    
    def get_env_vars(self, config: Dict[str, Any]) -> Dict[str, str]:
        """Get environment variables for QuantCode.
        
        Args:
            config: Instance configuration
            
        Returns:
            Environment variables
        """
        env = {}
        
        # Pass through API keys for LLM providers
        for key in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "MINIMAX_API_KEY"]:
            if key in os.environ:
                env[key] = os.environ[key]
        
        # Custom configuration
        if "llm_provider" in config:
            env["QUANTCODE_LLM_PROVIDER"] = config["llm_provider"]
        
        if "llm_model" in config:
            env["QUANTCODE_LLM_MODEL"] = config["llm_model"]
        
        return env
    
    async def send_analysis_request(
        self,
        instance: OpenCodeInstance,
        news_text: str,
        source: str = "telegram"
    ) -> Dict[str, Any]:
        """Send an analysis request to a QuantCode instance.
        
        Args:
            instance: QuantCode instance to use
            news_text: News article text
            source: Source identifier
            
        Returns:
            Analysis result
        """
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{instance.url}/tools/extraction",
                    json={
                        "news_text": news_text,
                        "source": source
                    }
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"Analysis request failed: {e}")
            return {"error": str(e)}
