# Connected scenes: plan, prove, then extend

Use this workflow when multiple shots depict one continuous event, or the creator reports disconnected clips, changing character proportions, or broken action handoffs. Individual usable takes do not establish a usable scene. Read this before changing storyboards, rendering replacements, or editing that sequence.

## Save one sequence plan

Use the existing project file tools to save a small production note under `refinement/`; keep the episode script and timeline as the source of executable prompts and edits. Do not invent script fields or a second asset registry. Update the note after each decision so another session can resume it.

Record:

- The story beat in one sentence: setup → cause → consequence → reaction. Preserve approved dialogue, identity, and story intent; camera choices and coverage may change.
- Distinguish creator-required events from director-invented blocking. An added exit, shrug, camera move, or target runtime is editable coverage, not automatically a story requirement. If it introduces drift, first test a shorter coherent edit or simpler reaction using existing footage. Do not retain a defect merely to finish your own planned gesture; do not remove the cause, consequence, or a creator-required event and call that a fix.
- The existing reference asset paths and a shared layout: character positions, ground plane, action direction, and the few landmarks needed to understand the event. State relative character size and anatomy in a neutral pose, independent of crouching or perspective. Do not turn incidental accessories or exact pixels into constraints.
- One master view that establishes the participants and the cause/effect relationship. Derive coverage from this layout and the same identity references. A new crop must not silently redesign the location, creature, or body proportions.
- A shot table: unit ID, narrative purpose, start state, one principal action, end state, motivated camera movement, and the handoff to the next shot. Each cut needs a reason: reveal, reaction, or continuation of action. Avoid redundant angle changes or a new camera move merely because a new clip starts.
- Sound across the sequence: approved continuous voice/music sources, where ambience continues, and any event that needs a synchronized effect. Regenerating visuals does not regenerate an approved voice or restart the music.
- Actual renderer inputs from capability inspection. A shared prompt or previous storyboard is not a carried video endpoint. Use endpoint/keyframe conditioning only when the configured route supports it. With first-image-only rendering, choose coverage that tolerates the limitation, inspect the actual outgoing action state, and prepare the next start accordingly; do not claim a precise match from unrelated start frames.

Inspect the actual provider request preview, not only the script notes. Planning descriptions and reference-sheet text are not necessarily sent to the video model. Put the essential actor, action cause, motion cadence, and camera relationship in the executable video prompt itself. A bag opening from inside must not become its wearer opening it; distinguish those performances before rendering and check the observed actor afterward. For ordinary walking, direct a continuous real-time stride with grounded footfalls. Compare runtime frame rate and source timestamps before blaming slow-looking motion on playback speed. Keep an approved continuous soundtrack on the edit timeline; raw per-shot audio is not a substitute.

Before rendering, inspect the planned start images together in narrative order. Correct conflicting scale, layout, or action state before spending on motion. This is a visual planning check, not proof that the later video will stay stable.

For walking or driving, trace the intended path on the visible ground plane. Screen-left-to-right is a projection, not a route: on a receding sidewalk it can direct a pedestrian across the curb into traffic. Check that the subject's forward direction follows the intended support surface and that approaching vehicles do not intersect that path unless the story calls for it. If the frame contradicts the motion direction, change the starting composition and motion instructions together before rendering. Keep incidental traffic out of the action when it adds no story value; do not substitute a frozen moving vehicle.

## Prove a connected passage

When repairing an unreliable sequence workflow, first complete a short setup/action/reaction passage with at least two joins. This passage is a proof, not a substitute for a requested full episode. Preserve the earlier exported film and approved audio. Do not spend on extending the same recipe until this passage has been assessed.

Use the existing generation previews, durable batches, version selection, and correction ledger. This workflow does not reset retry budgets, authorize a new provider, or bypass tool admission. If no correction budget remains, an explicitly authorized single experiment is not a new automatic retry loop.

After each take, compare its actual beginning, middle, and end against its reference and neighboring take. A creature becoming taller, longer-limbed, or human-shaped is an identity defect. A character becoming shorter or broader is also an identity defect. Do not excuse these as perspective, standing up, or performance unless visible ground-plane, camera, and anatomy evidence supports that explanation. If the view cannot establish this, report it as unverified.

For travel, also inspect the actual trajectory across the whole retained span: foot or wheel contact, intended path, curb crossings, and vehicle heading versus displacement. A safe starting frame does not establish a safe path. Reject unintended roadway entry or a collision setup; cropping away the ground, an approaching vehicle, or the defective endpoint is not evidence that the motion was corrected. Save the failed version and observed times, and promote the correction as a reusable lesson only after inspecting its replacement and adjacent join.

## Review the joins, then the assembly

Provide `review-footage` the sequence plan, exact selected versions and source ranges, the shared reference images, and neighboring shots. Review the outgoing end and incoming start together, including a short span on either side rather than only two stills. Check:

1. **Identity through time:** face, silhouette, limb proportions, and relative scale within each take and across cuts.
2. **Action and geography:** the cause visibly produces the consequence; movement, eyeline, object state, and important landmarks carry through. Natural pose changes are acceptable; unexplained teleportation, reversal, duplication, or growth is not.
3. **Editorial flow:** the cut advances the event and avoids repeated action, dead holds, or a pose jump. Prefer trimming or an existing better version when that fixes the observed problem. Dissolves do not repair changing anatomy or incompatible geography.
4. **Sound:** the continuous approved voice/music stays aligned with the assembled timing. Silent contact sheets cannot verify sound, fluid movement, or the full viewing experience; state those limits separately.

Save a sequence review with the selected versions/ranges, each join's time, observed evidence, unresolved defects, and the next smallest causal correction. Use `pass`, `needs_work`, or `unverified` separately for visual identity, action handoffs, and audio. The sequence is not a continuity pass if a material identity or handoff defect remains. A successful render, usable individual shots, or a review receipt cannot override that verdict.

Assemble and inspect the actual export before delivery. When tools cannot assess motion or audio, deliver a clearly labeled review cut and identify the missing evidence instead of declaring it finished. Preserve useful outputs, record what failed, and stay within the existing bounded correction process. Reuse only changes supported by before/after evidence; do not accumulate speculative prompt rules.
