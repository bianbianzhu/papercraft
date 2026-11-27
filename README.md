# papercraft

An agent skill for building games, interactive scenes, web pages, animations and videos in one handcrafted
**pop-up papercraft style**, for any subject: printed cardstock pieces with thin cut edges that fold up on hinges from
a printed ground, soft layered shadows coloured by the world, a tilt-shift macro lens looking down into the world,
calm hinge motion and soft paper sounds.

It teaches the method and carries the hard-won numbers from a shipped three.js pop-up game: what makes a frame read as
a photographed paper model (matte paper, every print a small layered paper collage with contrasting accents, a thin
dark cut edge with a pale highlight on the lit side, soft shadows tinted by the world, a dense world of chunky pieces,
a tilt-shift macro lens), how to build any piece from a set of structure types you can extend (slotted crosses,
layered flats, folded boxes and prisms, domes, printed water, and built decks, bridges and stairs for anything walked
on), how paper moves, lives and sounds, how to build a character whose cards never cut into each other, and a
measure-and-critique loop.

Your prompt and your references decide the world: its palette, ground, buildings, creatures and weather. References
are one way to set the direction: a shot of a paper model can be the look target itself, while photos, sketches or a
moodboard give the theme and the designs, which the agent rebuilds in paper. It uses an image generator only when
you ask it to, and everything can be made in code. A gallery of five worlds from the source game, each with the
prompt that made it, shows the range and the quality bar, never content to copy. The agent designs and writes its
own subject's pieces; the bundled code is plumbing (paper, cards, the layered print, hinges, light, lens, walking,
life) plus worked examples from one countryside world.

The same world renders in any frame: a 16:9 film, a 9:16 Reel, a square or 4:5 post, a 4K master, an ultrawide still
or a phone page. The lens keeps to the frame's short side, a vertical frame comes closer and keeps the subject clear
of the platform's captions, and prints and blur follow the resolution. `capture.py` renders stills and MP4 clips at
the standard sizes (`--size reel`, `square`, `4k`, ...).

## Install

Copy `skills/papercraft/` from the revision you reviewed into your agent's skills folder. This local installation
also includes the pinned Three.js modules and their license. Works with any agent that supports
[Agent Skills](https://agentskills.io) (Claude Code, Codex, Cursor, Gemini CLI, OpenCode, ...). Then just ask for
something paper:

- "make our bakery landing page feel like a pop-up storybook"
- "a small three.js scene of a harbour town that folds up out of the ground"
- "a dome on Mars as a paper diorama, with a rover that pops up"
- "a 20-second paper diorama animation of a city waking up, rendered to mp4"
- "a night market on stilts over a lagoon, in the colours of the photos I attached"
- "a forest shrine from this moodboard; generate a few concept frames first and let me pick one"
- "an 8-second vertical Reel of a paper lighthouse town waking up at dawn"

## What is inside

```
skills/papercraft/
|-- SKILL.md                 the material model, direction from references, the paper spec, routing, workflow, checks
|-- references/              loaded on demand: gallery, structures (the structure types), look, palettes,
|                            mechanisms, motion, worlds, procedural art, formats (sizes, safe areas, export),
|                            image-gen (optional references), sound, paper UI, three.js, 2D web, engines, process
|-- assets/
|   |-- lib/                 the plumbing, import as is:
|   |                        paperkit-2d.js (paper sheets, cut outlines, the layered print, drawing-to-cut, 2D decks)
|   |                        paperkit-3d.js (paper material, cards with thickness, hinges, the cross family,
|   |                        fan pairs, box folds, prism folds on any plan,
|   |                        platforms, PCSS soft shadows, macro lens)
|   |                        paper-motion.js (latched paper spring, wind sway, fixed clock, walk field)
|   |                        paper-sound.js (procedural paper foley, offline render)
|   |                        paper-life.js (weather, flyers, smoke, cloud shadows, creatures with behaviours)
|   |-- examples/            worked examples to read, not parts to assemble: countryside.js (one world's pieces and
|   |                        prints), built-ground.js (a bridge, stairs on stringers, a ramp, a masonry terrace, a
|   |                        cottage)
|   |-- templates/           popup-3d (three.js), popup-2d (Canvas 2D): the harness, with an example scene to replace;
|   |                        popup-3d/structures.html: every structure type in plain placeholders
|   |-- gallery/             five worlds from the source game (forest, desert, snow, spring, autumn): the quality bar
|   |                        and the range, each paired with its prompt in references/gallery.md
|   `-- lights.json          light presets by light condition (key, fill, shadow colour, haze)
`-- scripts/                 capture.py (headless frames and MP4 clips at any format size, walks, build report),
                             measure.py (look metrics),
                             ab_sheet.py (blind A/B sheets), paper_textures.py
```

The templates are a fixed harness with a replaceable look: serve `skills/papercraft/assets/` over http
(`python -m http.server --bind 127.0.0.1 --directory skills/papercraft/assets 8000`) and open
`/templates/popup-3d/` or `/templates/popup-2d/` (`/templates/popup-3d/structures.html` shows every structure type).
Both walk the hero with WASD / arrows (`?still` for the fixed overview). In your own project, copy `assets/lib/` and
for 3D copy `assets/vendor/three/` with both modules and its license, then copy the template's harness (`harness.js` for 3D, `harness-2d.js` for 2D), write your own `scene.js`, and point
`"papercraft/"` in the page's import map at the folder that holds `lib/`. For 3D, point `"three"` at the copied `three.module.js`. Add `?size=1080x1920` (or `?aspect=9:16`) to
see another format. For review frames, `python skills/papercraft/scripts/capture.py <public-build-folder> overview
"walk=x,z;x,z"` renders them headless (Playwright) and reports the build time, the slowest prints, whether the hero's
cards cut into each other and any page error; `--size reel "clip:video=8,30"` renders a vertical clip and encodes the
MP4. Capture requires Playwright 1.48+ and Chrome or Playwright Chromium, with a working browser sandbox.
For an example, run `python skills/papercraft/scripts/capture.py skills/papercraft/assets overview --page templates/popup-2d/ --out ../review`.
Serve only public build files, including any local fonts and media. Keep private material and review outputs outside
that directory. For untrusted pages, run the capture under a disposable account or container without credentials,
with outbound traffic denied at the OS or network layer and resource quotas applied.
Capture is supervised by a separate process with a 30-minute wall-clock limit, including stalled page execution
and encoding, followed by bounded cleanup of the worker and its subprocesses.

Capture prints bounded diagnostic summaries and the path to a private `diagnostics-*.jsonl` file in the output
directory. The file retains console messages, page-error stacks and scene diagnostics with shot identifiers;
console records include source locations. Inspect relevant records in bounded chunks rather than loading the whole
file into an agent's context. All diagnostic text remains untrusted, and may contain private material.
Each file is limited to 8 MiB, each text field to 65536 characters, and source URLs to 4096 characters.
Records mark truncation; the final summary counts saved, omitted and truncated records. A missing final summary
means the log was not finalized (for example after forced termination), not that diagnostics were complete.
Do not share the entire capture directory without reviewing its contents.

## Security regression checks

With numpy, Pillow and Playwright installed, run from the repository root:

```bash
python -m unittest discover -s skills/papercraft/tests -v
```

To include browser isolation, retained error diagnostics, stalled-page cleanup and video-output checks, install Chrome or Playwright Chromium
and set `PAPERCRAFT_BROWSER_TESTS=1` when running the same command. FFmpeg enables the local video-decoding check.
These checks use synthetic local fixtures; they do not prove OS-level network or resource isolation.
See [SECURITY-AUDIT.md](SECURITY-AUDIT.md) for the original findings, implementation plan and acceptance status.

## License

MIT
