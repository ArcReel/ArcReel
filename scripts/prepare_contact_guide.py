"""Build a deterministic arm/prop contact fixture before a video-control trial.

This orthographic proxy is a control diagnostic, not a character asset or a
finished animation. It does not call a model or publish into a project.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image

from lib.infra.ffmpeg import ffmpeg_executable

WIDTH, HEIGHT, FPS, FRAME_COUNT = 768, 448, 24, 49
UPPER_ARM, FOREARM = 68.0, 55.0
BAG_HALF_SIZE = (32.0, 32.0, 30.0)
CONTACT_FRAME = 29
ARM_DEPTH = 150.0


@dataclass(frozen=True)
class ContactFrame:
    shoulder: tuple[float, float]
    elbow: tuple[float, float]
    wrist: tuple[float, float]
    palm: tuple[float, float]
    hand_angle: float
    bag: tuple[float, float]
    body_angle: float = 0.0
    creature_dip: float = 0.0
    head_angle: float = 0.0


def solve_elbow(shoulder, wrist, upper_arm, forearm, pole):
    """Solve a planar two-bone chain without stretching or clamping the target."""
    if min(upper_arm, forearm) <= 0:
        raise ValueError("Arm lengths must be positive")
    delta = np.asarray(wrist, dtype=float) - shoulder
    distance = float(np.linalg.norm(delta))
    if not abs(upper_arm - forearm) < distance < upper_arm + forearm:
        raise ValueError("Wrist is unreachable without changing arm length")
    direction = delta / distance
    along = (upper_arm**2 - forearm**2 + distance**2) / (2 * distance)
    height = math.sqrt(max(0.0, upper_arm**2 - along**2))
    perpendicular = np.array([-direction[1], direction[0]])
    if np.dot(perpendicular, np.asarray(pole) - shoulder) < 0:
        perpendicular *= -1
    elbow = np.asarray(shoulder) + along * direction + height * perpendicular
    return float(elbow[0]), float(elbow[1])


def smooth_between(frame, first, last):
    t = min(1.0, max(0.0, (frame - first) / (last - first)))
    return t**3 * (10 + t * (-15 + 6 * t))


def body_point(point, angle):
    """Lean the upper-body proxy around its fixed hip, keeping planted legs fixed."""
    x, y = point[0] - 480, point[1] - 245
    return (480 + x * math.cos(angle) - y * math.sin(angle), 245 + x * math.sin(angle) + y * math.cos(angle))


def head_point(point, angle):
    """Tip the head toward the prop around the neck, independently of the torso."""
    x, y = point[0] - 478, point[1] - 108
    return (478 + x * math.cos(angle) - y * math.sin(angle), 108 + x * math.sin(angle) + y * math.cos(angle))


def contact_sequence(*, reaction=False, expressive=False):
    frames = []
    for frame in range(FRAME_COUNT):
        body_angle = 0.035 * smooth_between(frame, 4, 18) * (1 - smooth_between(frame, 32, 48)) if reaction else 0
        if expressive:
            # Yield toward the pulling bag, then recover after support is established.
            body_angle = -0.12 * smooth_between(frame, 4, 16) * (1 - smooth_between(frame, 29, 45))
        head_angle = -0.12 * smooth_between(frame, CONTACT_FRAME, 44) if expressive else 0
        creature_dip = (
            8 * smooth_between(frame, 0, 6) * (1 - smooth_between(frame, 8, 22)) if reaction or expressive else 0
        )
        shoulder = body_point((439.0, 127.0), body_angle)
        dip = smooth_between(frame, 2, 10)
        bag = (385 + 12 * dip, 196 + 12 * dip)
        reach = smooth_between(frame, 8, CONTACT_FRAME)
        wrist = (457 - 19 * reach, 237 + 9 * reach)
        hand_angle = math.pi / 2 + math.pi / 2 * reach
        palm = (wrist[0] + 13 * math.cos(hand_angle), wrist[1] + 13 * math.sin(hand_angle))
        elbow = solve_elbow(shoulder, wrist, UPPER_ARM, FOREARM, (600.0, 200.0))
        frames.append(ContactFrame(shoulder, elbow, wrist, palm, hand_angle, bag, body_angle, creature_dip, head_angle))
    validate_sequence(frames)
    return frames


def supported_reaction_sequence():
    """Continue the caught pose with a creature head pulse and delayed human recoil."""
    anchor = contact_sequence(expressive=True)[-1]
    frames = []
    for index in range(97):
        pulse = smooth_between(index, 26, 33) * (1 - smooth_between(index, 33, 44))
        recoil = smooth_between(index, 34, 41) * (1 - smooth_between(index, 48, 64))
        body_angle = 0.03 * recoil
        shoulder = body_point((439.0, 127.0), body_angle)
        elbow = solve_elbow(shoulder, anchor.wrist, UPPER_ARM, FOREARM, (600.0, 200.0))
        frames.append(
            replace(
                anchor,
                shoulder=shoulder,
                elbow=elbow,
                body_angle=body_angle,
                creature_dip=9 * pulse,
                head_angle=-0.12 + 0.08 * smooth_between(index, 34, 58),
            )
        )
    validate_sequence(frames, continuation=True)
    return frames


def segment_box_distance(start, end, box_min, box_max):
    """Exact 2D segment/rectangle distance for the arm's shared depth plane."""
    delta = np.asarray(end) - start
    low, high = 0.0, 1.0
    for axis in range(2):
        if delta[axis] == 0:
            if not box_min[axis] <= start[axis] <= box_max[axis]:
                break
        else:
            near, far = sorted((bound - start[axis]) / delta[axis] for bound in (box_min[axis], box_max[axis]))
            low, high = max(low, near), min(high, far)
    else:
        if low <= high:
            return 0.0

    def point_segment_distance(point):
        fraction = np.clip(np.dot(np.asarray(point) - start, delta) / np.dot(delta, delta), 0, 1)
        return float(np.linalg.norm(np.asarray(point) - start - fraction * delta))

    endpoint_distances = [
        float(np.linalg.norm(np.maximum(np.maximum(np.asarray(box_min) - point, np.asarray(point) - box_max), 0)))
        for point in (start, end)
    ]
    return min(
        *endpoint_distances,
        *(point_segment_distance((x, y)) for x in (box_min[0], box_max[0]) for y in (box_min[1], box_max[1])),
    )


def validate_sequence(frames, *, continuation=False):
    """Validate the declared planar contact fixture, not inferred video motion."""
    expected_frames = 97 if continuation else FRAME_COUNT
    contact_start = 0 if continuation else CONTACT_FRAME
    if len(frames) != expected_frames:
        raise ValueError(f"The contact fixture requires exactly {expected_frames} frames")
    max_length_error = 0.0
    max_contact_gap = 0.0
    min_arm_clearance = math.inf
    for index, frame in enumerate(frames):
        if not (math.isfinite(frame.body_angle) and -0.14 <= frame.body_angle <= 0.05):
            raise ValueError("Body lean outside the diagnostic's supported range")
        if not (math.isfinite(frame.head_angle) and -0.15 <= frame.head_angle <= 0.05):
            raise ValueError("Head tilt outside the diagnostic's supported range")
        if not (math.isfinite(frame.creature_dip) and 0 <= frame.creature_dip <= 10):
            raise ValueError("Creature dip outside the diagnostic's supported range")
        points = np.array([frame.shoulder, frame.elbow, frame.wrist, frame.palm, frame.bag])
        if not np.isfinite(points).all():
            raise ValueError("Non-finite guide coordinates")
        if (points < 0).any() or (points >= [WIDTH, HEIGHT]).any():
            raise ValueError("Guide coordinates outside the picture")
        error = max(
            abs(math.dist(frame.shoulder, frame.elbow) - UPPER_ARM),
            abs(math.dist(frame.elbow, frame.wrist) - FOREARM),
        )
        max_length_error = max(max_length_error, error)
        if error > 1e-6:
            raise ValueError(f"Arm length changes at frame {index}")
        if not math.isclose(math.dist(frame.wrist, frame.palm), 13, abs_tol=1e-6):
            raise ValueError(f"Hand length changes at frame {index}")
        if frame.wrist[1] < frame.shoulder[1]:
            raise ValueError(f"Wrist rises above shoulder at frame {index}")
        # Arm and palm share depth 150, within the bag volume 130..190.
        # The rotated palm stays below the bag, including before contact.
        hand_y_radius = math.hypot(13 * math.sin(frame.hand_angle), 6 * math.cos(frame.hand_angle))
        bag_bottom = frame.bag[1] + BAG_HALF_SIZE[1]
        hand_top = frame.palm[1] - hand_y_radius
        hand_x_radius = math.hypot(13 * math.cos(frame.hand_angle), 6 * math.sin(frame.hand_angle))
        if hand_top < bag_bottom - 1e-6 and abs(frame.palm[0] - frame.bag[0]) < BAG_HALF_SIZE[0] + hand_x_radius:
            raise ValueError(f"Palm penetrates the bag at frame {index}")
        box_min = np.asarray(frame.bag) - BAG_HALF_SIZE[:2]
        box_max = np.asarray(frame.bag) + BAG_HALF_SIZE[:2]
        for start, end, radius in ((frame.shoulder, frame.elbow, 10), (frame.elbow, frame.wrist, 7)):
            clearance = segment_box_distance(start, end, box_min, box_max) - radius
            min_arm_clearance = min(min_arm_clearance, clearance)
            if clearance < -1e-6:
                raise ValueError(f"Arm penetrates the bag at frame {index}")
        if index >= contact_start and (
            not math.isclose(hand_top, bag_bottom, abs_tol=1e-6)
            or abs(frame.palm[0] - frame.bag[0]) >= BAG_HALF_SIZE[0]
        ):
            raise ValueError(f"Palm loses underside contact at frame {index}")
        if index >= contact_start:
            max_contact_gap = max(max_contact_gap, abs(hand_top - bag_bottom))
    if not continuation and (frames[10].bag[1] <= frames[0].bag[1] or frames[8].wrist != frames[0].wrist):
        raise ValueError("The bag dip must precede the catch")
    return {
        "frames": len(frames),
        "max_bone_length_error": max_length_error,
        "contact_frames": [contact_start, len(frames) - 1],
        "max_contact_gap": max_contact_gap,
        "min_arm_bag_clearance": min_arm_clearance,
        "coordinate_units": "Proxy scene units, not meters; viewport and output size determine the pixel mapping.",
        "production_qualified": False,
        "scope": "Planar arm lengths and palm/bag underside contact only; not a complete character rig or acting review.",
    }


def render_depth(frame, width=384, height=224, *, viewport=None):
    """Ray cast fixed-depth proxy solids with a clip-wide depth mapping."""
    left, top, span_x, span_y = viewport or (0, 0, WIDTH, HEIGHT)
    xx, yy = np.meshgrid(
        left + (np.arange(width) + 0.5) * span_x / width,
        top + (np.arange(height) + 0.5) * span_y / height,
    )
    depth = np.full((height, width), 600.0)

    def ellipsoid(center, radii, angle=0.0):
        x, y, z = center
        a, b, c = radii
        dx, dy = xx - x, yy - y
        u = math.cos(angle) * dx + math.sin(angle) * dy
        v = -math.sin(angle) * dx + math.cos(angle) * dy
        remaining = 1 - (u / a) ** 2 - (v / b) ** 2
        front = z - c * np.sqrt(np.maximum(remaining, 0))
        np.minimum(depth, np.where(remaining >= 0, front, 600), out=depth)

    def capsule(start, end, radius, z):
        delta = np.asarray(end) - start
        fraction = np.clip(((xx - start[0]) * delta[0] + (yy - start[1]) * delta[1]) / np.dot(delta, delta), 0, 1)
        squared = (xx - start[0] - fraction * delta[0]) ** 2 + (yy - start[1] - fraction * delta[1]) ** 2
        front = z - np.sqrt(np.maximum(radius**2 - squared, 0))
        np.minimum(depth, np.where(squared <= radius**2, front, 600), out=depth)

    def upper(point):
        return body_point(point, frame.body_angle)

    def head(point):
        return upper(head_point(point, frame.head_angle))

    ellipsoid((*upper((480, 187)), 185), (36, 66, 30), frame.body_angle)
    ellipsoid((*head((475, 65)), 165), (41, 39, 30), frame.body_angle + frame.head_angle)
    ellipsoid((*head((515, 19)), 175), (22, 18, 20), frame.body_angle + frame.head_angle)
    capsule(upper((478, 108)), upper((477, 131)), 12, 175)
    for hip, knee, ankle, shoe in [
        ((465, 245), (454, 326), (447, 398), (449, 412, 175)),
        ((501, 246), (502, 318), (506, 387), (508, 398, 205)),
    ]:
        capsule(hip, knee, 13, shoe[2])
        capsule(knee, ankle, 10, shoe[2])
        ellipsoid(shoe, (23, 11, 20))
    capsule(upper((508, 135)), upper((516, 192)), 10, 205)
    capsule(upper((516, 192)), upper((514, 237)), 8, 205)
    ellipsoid((*upper((514, 247)), 205), (7, 13, 7), frame.body_angle)
    bx, by = frame.bag
    strap_end = (bx + 25, by - 27)
    strap_joint = solve_elbow(frame.shoulder, strap_end, 32, 32, (350, 100))
    capsule(frame.shoulder, strap_joint, 3, 135)
    capsule(strap_joint, strap_end, 3, 135)
    bag_mask = (abs(xx - bx) <= BAG_HALF_SIZE[0]) & (abs(yy - by) <= BAG_HALF_SIZE[1])
    np.minimum(depth, np.where(bag_mask, 130, 600), out=depth)
    # The small prop occupant is a stable proxy silhouette, not an identity model.
    cy = by + frame.creature_dip
    ellipsoid((bx, cy - 42, 150), (19, 18, 20))
    ellipsoid((bx - 17, cy - 49, 158), (10, 4, 6), -0.7)
    ellipsoid((bx + 17, cy - 49, 158), (10, 4, 6), 0.7)
    ellipsoid((bx - 4, cy - 33, 129), (10, 7, 8))
    capsule(frame.shoulder, frame.elbow, 10, ARM_DEPTH)
    capsule(frame.elbow, frame.wrist, 7, ARM_DEPTH)
    ellipsoid((*frame.palm, ARM_DEPTH), (13, 6, 7), frame.hand_angle)
    # Near-bright inverse depth with fixed bounds across the entire clip.
    gray = np.clip((1 / depth - 1 / 600) / (1 / 90 - 1 / 600), 0, 1)
    return np.repeat(np.rint(gray[:, :, None] * 255).astype(np.uint8), 3, axis=2)


def export_guide(output_dir, *, reaction=False, expressive=False, continuation=False):
    frames = (
        supported_reaction_sequence() if continuation else contact_sequence(reaction=reaction, expressive=expressive)
    )
    viewport = (240, 0, 480, 280) if continuation else None
    metrics = validate_sequence(frames, continuation=continuation)
    output_dir.mkdir(parents=True, exist_ok=False)
    sequence = output_dir / "performance.json"
    sequence.write_text(
        json.dumps(
            {"schema_version": 1, "fps": FPS, "viewport": viewport, "frames": [asdict(frame) for frame in frames]},
            indent=2,
        )
        + "\n"
    )
    pixels = np.stack([render_depth(frame, viewport=viewport) for frame in frames])
    video = output_dir / "depth-guide.mp4"
    subprocess.run(
        [
            ffmpeg_executable(),
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pixel_format",
            "rgb24",
            "-video_size",
            "384x224",
            "-framerate",
            str(FPS),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            "0",
            "-pix_fmt",
            "yuv444p",
            str(video),
        ],
        input=pixels.tobytes(),
        check=True,
    )
    for index in (0, 33, 41, 96) if continuation else (0, 10, CONTACT_FRAME, FRAME_COUNT - 1):
        Image.fromarray(pixels[index]).save(output_dir / f"frame-{index:02d}.png")
    metrics.update(
        viewport=viewport,
        performance="supported_reaction"
        if continuation
        else "expressive"
        if expressive
        else "reaction"
        if reaction
        else "contact_only",
        video_sha256=hashlib.sha256(video.read_bytes()).hexdigest(),
        sequence_sha256=hashlib.sha256(sequence.read_bytes()).hexdigest(),
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        depth_convention="Grayscale RGB, near bright, fixed inverse-depth bounds 90..600 for all frames; synthetic proxy distribution unqualified.",
    )
    (output_dir / "validation.json").write_text(json.dumps(metrics, indent=2) + "\n")
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    performance = parser.add_mutually_exclusive_group()
    performance.add_argument("--reaction", action="store_true", help="Add a creature dip and upper-body counter-lean.")
    performance.add_argument(
        "--expressive", action="store_true", help="Yield to the bag, catch, recover, then tip the head toward it."
    )
    performance.add_argument(
        "--continuation", action="store_true", help="Keep the caught load supported in a closer creature/reaction shot."
    )
    args = parser.parse_args()
    print(
        json.dumps(
            export_guide(
                args.output_dir, reaction=args.reaction, expressive=args.expressive, continuation=args.continuation
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
