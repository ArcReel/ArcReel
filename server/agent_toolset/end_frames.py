"""Declare the director's ending-frame control."""

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.media_tools.end_frames import SetVideoEndFrameRequest, set_video_end_frame, set_video_end_frame_summary

SET_VIDEO_END_FRAME = ToolDeclaration(
    name="set_video_end_frame",
    description=(
        "Set a storyboard shot's ending-frame image from an existing project-relative image, "
        "or explicitly pass source_path=null to clear it. Uses the same snapshot service as the editor: "
        "the selected image is copied into end_frames, so later source changes cannot alter it. "
        "This changes guidance for the next generate_videos call; it does not generate or replace video. "
        "Inspect both images and check get_video_capabilities before use. Select a physically coherent "
        "ending state with the same identity, props, support and screen direction, not an unrelated cut. "
        "After generation, verify actual media_inputs contains the ending frame and review the entire "
        "motion; matching endpoints do not guarantee correct intermediate movement. "
        "Reference-video projects do not support this control."
    ),
    request_model=SetVideoEndFrameRequest,
    migration=BLOCKED,
    domain_key="set_video_end_frame",
    handler=set_video_end_frame,
    summary=set_video_end_frame_summary,
)

END_FRAME_TOOLS = (SET_VIDEO_END_FRAME,)
