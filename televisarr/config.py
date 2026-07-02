"""
Configuration loading and validation for Televisarr.
"""

import os
import sys
import time
import signal
import yaml
from typing import Optional

from televisarr import logger
from televisarr.schema import TelevisarrConfig


def env_constructor(loader, node):
    """YAML constructor for !env tags to load environment variables."""
    env_var = loader.construct_scalar(node)
    env_value = os.getenv(env_var)
    
    if env_value is None:
        message = f"Environment variable '{env_var}' is not set."
        logger.error(message)
        raise ValueError(message)
    
    return env_value


def hang_on_error(msg):
    """Log error and hang indefinitely to prevent restart loops."""
    logger.error(msg)
    logger.error("Container will stay idle. Fix the configuration and restart the container.")
    
    def shutdown_handler(signum, frame):
        logger.info("Received shutdown signal, exiting.")
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)
    
    # Sleep forever until manually stopped
    while True:
        time.sleep(3600)


def load_config(config_file: str) -> TelevisarrConfig:
    """
    Load and validate configuration from a YAML file.
    
    Args:
        config_file: Path to the YAML configuration file
        
    Returns:
        TelevisarrConfig: Validated configuration object
        
    Raises:
        SystemExit: If configuration is invalid or file not found
    """
    try:
        full_path = os.path.abspath(config_file)
        
        with open(full_path, "r", encoding="utf-8") as stream:
            logger.debug("Loading configuration from %s", full_path)
            return load_yaml(stream)
            
    except FileNotFoundError:
        hang_on_error(
            f"Configuration file {config_file} not found. "
            "Copy the example config and edit it to your needs."
        )
    except yaml.YAMLError as exc:
        hang_on_error(f"YAML parsing error: {exc}")


def load_yaml(stream) -> TelevisarrConfig:
    """
    Load YAML with custom !env tag support and validate with Pydantic.
    
    Args:
        stream: YAML file stream
        
    Returns:
        TelevisarrConfig: Validated configuration object
    """
    class CustomLoader(yaml.SafeLoader):
        pass
    
    CustomLoader.add_constructor('!env', env_constructor)
    
    # Load raw YAML
    raw_config = yaml.load(stream, Loader=CustomLoader)
    
    # Validate with Pydantic
    try:
        config = TelevisarrConfig(**raw_config)
        return config
    except Exception as e:
        hang_on_error(f"Configuration validation error: {e}")


def validate_connections(config: TelevisarrConfig) -> bool:
    """
    Validate connections to Plex and Sonarr.
    
    Args:
        config: Validated configuration object
        
    Returns:
        bool: True if all connections are successful
    """
    # Validate Plex connection
    try:
        from televisarr.modules.plex import PlexMediaServer
        plex = PlexMediaServer(
            config.plex.url,
            config.plex.token,
            ssl_verify=False  # Default to False for self-signed certs
        )
        plex.test_connection()
        logger.info("Plex connection successful")
    except Exception as e:
        logger.error(f"Failed to connect to Plex: {e}")
        return False
    
    # Validate Sonarr connection
    try:
        from televisarr.modules.sonarr import DSonarr
        sonarr = DSonarr(
            config.sonarr.name,
            config.sonarr.url,
            config.sonarr.api_key
        )
        sonarr.validate_connection()
        logger.info(f"Sonarr connection successful (instance: {config.sonarr.name})")
    except Exception as e:
        logger.error(f"Failed to connect to Sonarr: {e}")
        return False
    
    return True


def get_sonarr_instance(config: TelevisarrConfig) -> 'DSonarr':
    """
    Get the Sonarr instance from the configuration.
    
    Args:
        config: Validated configuration object
        
    Returns:
        DSonarr: Sonarr instance
    """
    from televisarr.modules.sonarr import DSonarr
    
    return DSonarr(
        config.sonarr.name,
        config.sonarr.url,
        config.sonarr.api_key
    )