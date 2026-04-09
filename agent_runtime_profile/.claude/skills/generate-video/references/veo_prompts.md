# Veo 3.1 Video Generation Prompt Guide

Best practices for creating effective prompts for Veo 3.1 video generation.

## Prompt Structure

Following Veo best practices, prompts should include the following elements (naturally blended, no tags):

1. **Composition**: shot type (wide shot, close-up, medium shot)
2. **Subject**: scene description including characters, environment, objects
3. **Action**: what the characters are doing
4. **Dialogue**: Speaker (manner) says: "text"
5. **Sound Effects**: naturally integrated into the scene description
6. **Camera**: natural description of the camera movement
7. **Ambiance**: lighting and mood

**Important**: do not include the following in the prompt (these are passed via API parameters):
- Video duration (e.g., "8 seconds")
- Aspect ratio (e.g., "16:9", "9:16")

## Dialogue and Audio

### Dialogue Format
```
The man (gripping a hunting knife) says: "This is no ordinary bear."
The woman (voice taut with fear, looking around) says: "What is it then?"
```

Wrap dialogue content in quotation marks; describe actions and speaking manner in parentheses.

### Speaking Manner Descriptions
- `softly`, `in a whisper`, `shouting`, `muttering`
- `gently`, `nervously`, `resolutely`
- `in a deep male voice`, `in a clear female voice`

### Sound Effects (Naturally Integrated)
Do not use "Sound effects:" labels; instead, describe naturally:
```
A rough bark, the sound of snapping branches, footsteps on wet soil. A lone bird cries.
```

```
The sharp squeal of tires, the roar of an engine.
```

### About BGM
- **Do not describe background music in the prompt**
- BGM is automatically excluded via the `negative_prompt` parameter
- Add background music in post-production using `/compose-video`

## Camera Movement

| English Term | Description |
|---------|---------|
| static | Camera still |
| pan left/right | Camera pans left/right |
| tilt up/down | Camera tilts up/down |
| dolly in/out | Camera slowly pushes in/pulls back |
| track left/right | Camera tracks left/right |
| crane up/down | Camera cranes up/down |
| handheld | Handheld camera with slight shake |

## Shot Types

| English Term | Chinese Term | Suitable Scenes |
|---------|---------|---------|
| extreme close-up | Extreme close-up | Emotion, detail |
| close-up | Close-up | Face, dialogue |
| medium shot | Medium shot | Upper body, dialogue |
| full shot | Full shot | Full body |
| wide shot | Wide shot | Environment, establishing shot |
| aerial | Aerial | Bird's-eye view |

## Negative Prompts

Use the `negative_prompt` API parameter to exclude unwanted elements:
- Do not use negative language: "no walls"
- Directly describe what you don't want: "walls, frames, borders"

Default negative prompt (automatically applied):
```
background music, BGM, soundtrack, musical accompaniment
```

## Examples

### Dialogue and Atmosphere Scene
```
wide shot, fog-shrouded Pacific Northwest forest. Two exhausted hikers, a man and a woman, push through ferns; the man suddenly stops, staring at a tree. close-up: deep fresh claw marks in the bark. The man (gripping a hunting knife) says: "This is no ordinary bear." The woman (voice taut with fear, looking around) says: "What is it then?" A rough bark, the sound of snapping branches, footsteps on wet soil. A lone bird cries.
```

### Detailed Scene
```
close-up cinematic shot following a desperate man in a worn green trench coat dialing a rotary phone on a rough brick wall, bathed in the eerie glow of green neon lights. The camera slowly pushes in, revealing the tension in his jaw and the desperation etched on his face as he struggles to make the call. Shallow depth of field focuses on his furrowed brow and the black rotary phone, while the background blurs into a sea of neon colors and blurred shadows, creating a sense of urgency and isolation.
```

### Animated Style Scene
```
A cheerful cartoon-style 3D animated scene. A cute creature with snow leopard fur, large expressive eyes, and a friendly rounded form bounces joyfully through a whimsical winter forest. The scene features rounded snow-covered trees, gently falling snowflakes, and warm sunlight filtering through the branches. The creature's bouncy movements and bright smile convey pure joy. Bright, cheerful colors and lively animation, warm and heartwarming tone.
```

### Image-to-Video Scene
```
A surreal, cinematic macro video. A tiny surfer rides endless rolling waves in a stone sink. A vintage brass faucet is running, creating endless waves. The camera slowly pans across this whimsical, sun-drenched scene, with a miniature figure skillfully paddling on the emerald water surface.
```
