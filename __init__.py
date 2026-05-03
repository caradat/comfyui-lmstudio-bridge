"""
ComfyUI LM Studio Bridge - Integration with local LLM models
"""

from .lmstudio_bridge_node import LMStudioBridge

# Define how ComfyUI maps the node name (used in backend) to the class
NODE_CLASS_MAPPINGS = {
    "LMStudioBridge": LMStudioBridge
}

# Define how ComfyUI maps the node name to its display name (shown in the UI)
NODE_DISPLAY_NAME_MAPPINGS = {
    "LMStudioBridge": "LM Studio Bridge"
}

# Standard dictionary telling ComfyUI what this package provides
__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS']

print("--- ComfyUI LM Studio Bridge Loaded ---")
