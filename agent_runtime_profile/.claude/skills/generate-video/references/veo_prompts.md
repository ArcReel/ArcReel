# Veo 3.1 video prompt guide

Best practices for writing Veo 3.1 video generation prompts.

## Prompt structure

Following Veo best practices, include these elements naturally, without labels:

1. **Composition**: shot type (wide shot, close-up, medium shot)
2. **Subject**: scene description, including characters, environment, and objects
3. **Action**: what characters are doing
4. **Dialogue**: speaker (delivery) says: "dialogue text"
5. **Sound Effects**: naturally integrated into scene descriptions
6. **Camera**: natural descriptions of movement
7. **Ambiance**: lighting and mood

**Important**: do not write the following in prompts (they are supplied through API parameters):
- Video duration (such as "8 seconds")
- Aspect ratio (such as "16:9", "9:16")

## Dialogue and audio

### Dialogue format
```
The man (gripping a hunting knife) says: "This is no ordinary bear."
The woman (voice tight with fear, looking around) says: "What is it?"
```

Enclose dialogue in quotation marks; describe actions and delivery in parentheses.

### Speech delivery descriptions
- `Softly`, `whispering`, `shouting`, `muttering to oneself`
- `Gently`, `nervously`, `firmly`
- `A low male voice`, `a clear female voice`

### Sound effects (naturally integrated)
Do not use a "Sound effects:" label; describe naturally:
```
A rough bark, branches snapping, footsteps on damp soil. A lone bird calls.
```

```
Tires screech sharply, an engine roars.
```

### BGM
- **Do not describe background music in prompts**
- The generator automatically appends BGM / subtitle / watermark exclusions; no manual handling is needed

## Camera movement

| Term | Description |
|---------|---------|
| static | Camera remains still |
| pan left/right | Camera pans left/right |
| tilt up/down | Camera tilts up/down |
| dolly in/out | Camera slowly moves forward/backward |
| track left/right | Camera tracks left/right |
| crane up/down | Camera rises/descends |
| handheld | Slight handheld camera shake |

## Shot scale

| English term | Shot description | Use |
|---------|---------|---------|
| extreme close-up | Very tight detail | Emotion, details |
| close-up | Close framing | Faces, dialogue |
| medium shot | Medium framing | Upper body, dialogue |
| full shot | Full framing | Full body |
| wide shot | Distant framing | Environment, establishing shots |
| aerial | Overhead framing | Bird's-eye view |

## Negative prompts

List excluded elements directly rather than using negative sentences:
- ❌ Negative wording: "no walls"
- ✅ Direct list: "walls, frames, borders"

The generator automatically appends BGM / subtitle / watermark exclusions; no manual handling is needed.

## Examples

### Dialogue and atmosphere
```
Wide shot, a fog-shrouded Pacific Northwest forest. Two exhausted hikers, a man and a woman, push through ferns. The man suddenly stops, staring at a tree. Close-up: fresh, deep claw marks in the bark. The man (gripping a hunting knife) says: "This is no ordinary bear." The woman (voice tight with fear, looking around) says: "What is it?" A rough bark, branches snapping, footsteps on damp soil. A lone bird calls.
```

### Detailed description
```
A cinematic close-up follows a desperate man in a worn green trench coat using a rotary phone mounted on a rough brick wall, bathed in the eerie glow of green neon. The camera slowly dollies in, revealing his clenched jaw and desperation etched into his face as he struggles to connect the call. Shallow depth of field focuses on his furrowed brow and the black rotary phone, blurring the background into a sea of neon colors and indistinct shadows, creating urgency and isolation.
```

### Animation style
```
A cheerful, cartoon-style 3D animated scene. An adorable creature with snow-leopard-like fur, large expressive eyes, and friendly rounded features happily bounces through a whimsical winter forest. Rounded snow-covered trees, gently falling snowflakes, and warm sunlight filtering through branches fill the scene. The creature's bouncing movements and radiant smile convey pure joy. Bright, cheerful colors and lively animation create a warm, comforting tone.
```

### Storyboard-to-video
```
A surreal, cinematic macro video. Tiny surfers ride endlessly rolling waves in a stone washbasin. An old brass faucet runs, creating endless surf. The camera slowly pans across this whimsical, sunlit scene as miniature figures skillfully paddle on emerald water.
```
