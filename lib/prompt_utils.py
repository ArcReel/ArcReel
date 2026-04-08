"""
Prompt utility functions

Provides conversion from structured prompts to YAML format.
"""

import yaml

# Preset option definitions
STYLES = ["Photographic", "Anime", "3D Animation"]

SHOT_TYPES = [
    "Extreme Close-up",
    "Close-up",
    "Medium Close-up",
    "Medium Shot",
    "Medium Long Shot",
    "Long Shot",
    "Extreme Long Shot",
    "Over-the-shoulder",
    "Point-of-view",
]

CAMERA_MOTIONS = [
    "Static",
    "Pan Left",
    "Pan Right",
    "Tilt Up",
    "Tilt Down",
    "Zoom In",
    "Zoom Out",
    "Tracking Shot",
]


def image_prompt_to_yaml(image_prompt: dict, project_style: str) -> str:
    """
    Convert an imagePrompt structure to a YAML-format string.

    Args:
        image_prompt: The image_prompt object from a segment, with the structure:
            {
                "scene": "scene description",
                "composition": {
                    "shot_type": "shot type",
                    "lighting": "lighting description",
                    "ambiance": "ambiance description"
                }
            }
        project_style: Project-level style setting (read from project.json)

    Returns:
        YAML-format string for use in Gemini API calls
    """
    ordered = {
        "Style": project_style,
        "Scene": image_prompt["scene"],
        "Composition": {
            "shot_type": image_prompt["composition"]["shot_type"],
            "lighting": image_prompt["composition"]["lighting"],
            "ambiance": image_prompt["composition"]["ambiance"],
        },
    }
    return yaml.dump(ordered, allow_unicode=True, default_flow_style=False, sort_keys=False)


def video_prompt_to_yaml(video_prompt: dict) -> str:
    """
    Convert a videoPrompt structure to a YAML-format string.

    Args:
        video_prompt: The video_prompt object from a segment, with the structure:
            {
                "action": "action description",
                "camera_motion": "camera motion",
                "ambiance_audio": "ambient audio description",
                "dialogue": [{"speaker": "character name", "line": "dialogue line"}]
            }

    Returns:
        YAML-format string for use in Veo API calls
    """
    dialogue = [{"Speaker": d["speaker"], "Line": d["line"]} for d in video_prompt.get("dialogue", [])]

    ordered = {
        "Action": video_prompt["action"],
        "Camera_Motion": video_prompt["camera_motion"],
        "Ambiance_Audio": video_prompt.get("ambiance_audio", ""),
    }

    # Only add the Dialogue field when there is dialogue
    if dialogue:
        ordered["Dialogue"] = dialogue

    return yaml.dump(ordered, allow_unicode=True, default_flow_style=False, sort_keys=False)


def is_structured_image_prompt(image_prompt) -> bool:
    """
    Check whether image_prompt is in structured format.

    Args:
        image_prompt: Value of the image_prompt field

    Returns:
        True if in structured format (dict), False if in the legacy string format
    """
    return isinstance(image_prompt, dict) and "scene" in image_prompt


def is_structured_video_prompt(video_prompt) -> bool:
    """
    Check whether video_prompt is in structured format.

    Args:
        video_prompt: Value of the video_prompt field

    Returns:
        True if in structured format (dict), False if in the legacy string format
    """
    return isinstance(video_prompt, dict) and "action" in video_prompt


def validate_style(style: str) -> bool:
    """Validate whether the style is one of the preset options."""
    return style in STYLES


def validate_shot_type(shot_type: str) -> bool:
    """Validate whether the shot type is one of the preset options."""
    return shot_type in SHOT_TYPES


def validate_camera_motion(camera_motion: str) -> bool:
    """Validate whether the camera motion is one of the preset options."""
    return camera_motion in CAMERA_MOTIONS
