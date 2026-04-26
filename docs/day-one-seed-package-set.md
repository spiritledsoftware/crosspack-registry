# Crosspack Registry day-one seed package set recommendation

Date: 2026-03-31
Issue: SPI-11
Project: Crosspack Registry

## Executive recommendation

Recommended day-one seed set of 5:
1. crosspack
2. ripgrep
3. fd
4. uv
5. gh

Why this 5:
- strongest demo value for Crosspack’s core promise: trustworthy cross-platform CLI installs
- high recognition among developers on Windows, macOS, and Linux
- mostly simple GitHub-release sourcing with deterministic asset naming
- low metadata risk relative to GUI apps and vendor-specific installers
- enough breadth to demo search, info, install, upgrade, checksums, signatures, and shell integration without turning the registry into a support burden

Confidence: medium-high

## Selection criteria used

Weighted highest:
- cross-platform coverage for Linux + macOS + Windows
- upstream release stability and machine-friendly asset naming
- install trust/demo value for developer audiences
- low ongoing metadata maintenance burden
- low first-wave platform caveat risk

Weighted lower for day one:
- large GUI apps with heavier platform-specific packaging
- packages with uneven target coverage
- packages that are valuable later but do not sharpen the initial Crosspack story

## Current candidate universe (10-15 packages to support first)

These are the strongest current candidates from the existing registry/source-config set.

### Tier 1: recommended day-one seed set

#### 1) crosspack
Why it matters
- essential trust demo: users can inspect how Crosspack packages itself
- proves registry automation, release ingestion, and self-update path
- gives the project an obvious canonical package to validate end to end

Upstream source
- GitHub releases: spiritledsoftware/crosspack
- Source config: `registry/sources/crosspack.toml`

Version/update strategy
- automated from GitHub releases with `tag_prefix = "v"`
- should stay tightly coupled to Crosspack release workflow and registry-sync automation

Metadata requirements
- package template with 7 targets
- release docs with per-target HTTPS URL + SHA-256
- binaries: `crosspack`, plus Windows alias `cpk`
- tar.gz + zip only, no GUI handling

Platform caveats
- current source config covers strong breadth, including linux glibc and musl variants
- registry currently trails repo release line in `releases/crosspack/` (latest file present is 0.9.0 while main repo shows v0.10.x tags), so freshness monitoring matters

Decision note
- must be in day one because it makes the registry easier to trust

#### 2) ripgrep
Why it matters
- widely known CLI benchmark package
- perfect demo package for search/install/info flows
- includes shell completion metadata, which helps show Crosspack’s completion handling

Upstream source
- GitHub releases: BurntSushi/ripgrep
- Source config: `registry/sources/ripgrep.toml`

Version/update strategy
- automated from GitHub releases
- no explicit tag prefix needed; tags normalize cleanly

Metadata requirements
- 6 targets across Linux/macOS/Windows incl. arm64 variants
- tar.gz + zip
- `strip_components` differs by platform
- binaries: `rg`
- completions: bash, zsh, fish, powershell

Platform caveats
- Linux x86_64 target uses musl asset while target label is `x86_64-unknown-linux-gnu`; acceptable if install/runtime testing stays green, but it is worth documenting explicitly

Decision note
- one of the best possible “hello world” packages for developer adoption

#### 3) fd
Why it matters
- pairs naturally with ripgrep in demos
- high developer familiarity
- includes completions, which enriches install proof
- simple binary release pattern

Upstream source
- GitHub releases: sharkdp/fd
- Source config: `registry/sources/fd.toml`

Version/update strategy
- automated from GitHub releases with `tag_prefix = "v"`

Metadata requirements
- 6 targets across Linux/macOS/Windows incl. arm64 Windows
- tar.gz + zip
- completions for bash, zsh, fish, powershell
- binaries: `fd`

Platform caveats
- low caveat package overall
- keep an eye on archive internal paths because completion paths are archive-relative

Decision note
- very strong day-one package because it is simple, trusted, and demo-friendly

#### 4) uv
Why it matters
- high-growth developer tool with strong mindshare
- demonstrates that Crosspack can install modern dev workflow tooling, not just classic Unix utilities
- shipping both `uv` and `uvx` is a nice proof of multi-binary package support

Upstream source
- GitHub releases: astral-sh/uv
- Source config: `registry/sources/uv.toml`

Version/update strategy
- automated from GitHub releases
- no tag prefix required in source config

Metadata requirements
- 5 targets across Linux/macOS/Windows
- tar.gz + zip
- binaries: `uv`, `uvx`
- `strip_components = 1`

Platform caveats
- no Linux musl target in current config; acceptable for day one but reduces “runs everywhere” breadth versus crosspack/ripgrep/fd

Decision note
- best choice for proving relevance to active Python/AI/dev-tool users

#### 5) gh
Why it matters
- very recognizable developer utility
- helps position Crosspack as a serious CLI package manager, not a toy demo
- clean install story across major OSes

Upstream source
- GitHub releases: cli/cli
- Source config: `registry/sources/gh.toml`

Version/update strategy
- automated from GitHub releases with `tag_prefix = "v"`

Metadata requirements
- 5 targets across Linux/macOS/Windows
- tar.gz + zip
- binary: `gh`
- archive paths use `bin/gh` / `bin/gh.exe`

Platform caveats
- no Windows arm64 target in current config
- otherwise low-risk package

Decision note
- strong trust package because users already know what “good” distribution should look like here

### Tier 2: first expansion wave after day one

#### 6) jq
Why it matters
- extremely common utility and easy search/info/install demo
- metadata is simple because artifacts are direct binaries

Upstream source
- GitHub releases: jqlang/jq
- Source config: `registry/sources/jq.toml`

Version/update strategy
- automated from GitHub releases

Metadata requirements
- 5 targets
- binary assets, no archive extraction
- binary: `jq`

Platform caveats
- fewer bells and whistles than ripgrep/fd, so it is slightly less useful as a flagship demo even though it is easy to maintain

Why not day one
- very good package, but less distinctive as a flagship than ripgrep/fd/uv/gh

#### 7) fzf
Why it matters
- strong developer recognition
- good cross-platform CLI example

Upstream source
- GitHub releases: junegunn/fzf
- Source config: `registry/sources/fzf.toml`

Version/update strategy
- automated from GitHub releases with `tag_prefix = "v"`

Metadata requirements
- 6 targets incl. arm64 Windows
- tar.gz + zip
- binary: `fzf`

Platform caveats
- no completion metadata in current source config, so the demo story is slightly thinner than fd/ripgrep

Why not day one
- excellent second-wave package, but fd and ripgrep tell a better immediate story

#### 8) lazygit
Why it matters
- very popular dev CLI
- good proof that Crosspack can handle workflow tools beyond text utilities

Upstream source
- GitHub releases: jesseduffield/lazygit
- Source config: `registry/sources/lazygit.toml`

Version/update strategy
- automated from GitHub releases with `tag_prefix = "v"`

Metadata requirements
- 5 targets
- tar.gz + zip
- binary: `lazygit`

Platform caveats
- no major known caveats from current metadata

Why not day one
- great expansion package, but not as universally “obvious” as gh or ripgrep

#### 9) bat
Why it matters
- popular cross-platform CLI
- easy maintenance pattern

Upstream source
- GitHub releases: sharkdp/bat
- Source config: `registry/sources/bat.toml`

Version/update strategy
- automated from GitHub releases with `tag_prefix = "v"`

Metadata requirements
- 5 targets
- tar.gz + zip
- binary: `bat`

Platform caveats
- current source config does not capture shell completions, which lowers demo richness versus fd/ripgrep

Why not day one
- good package, but slightly redundant with fd/ripgrep in the first impression set

#### 10) delta
Why it matters
- respected developer CLI and good Git-adjacent tool

Upstream source
- GitHub releases: dandavison/delta
- Source config: `registry/sources/delta.toml`

Version/update strategy
- automated from GitHub releases

Metadata requirements
- 5 targets
- tar.gz + zip
- binary: `delta`

Platform caveats
- straightforward package; lower brand recognition than gh/ripgrep/fd

Why not day one
- useful but not essential to the initial demo set

### Tier 3: valuable later, but higher support or narrative cost

#### 11) starship
Why it matters
- high developer awareness
- shell-oriented tool that can broaden package mix

Upstream source
- GitHub releases: starship/starship
- Source config: `registry/sources/starship.toml`

Version/update strategy
- automated from GitHub releases with `tag_prefix = "v"`

Metadata requirements
- 5 targets
- tar.gz + zip
- binary: `starship`

Platform caveats
- Linux coverage is split between gnu and musl rather than a fuller symmetric target matrix

Why later
- useful, but day-one story is stronger with packages users execute directly after install for visible payoff

#### 12) bruno
Why it matters
- modern API client with increasing mindshare
- strong proof that Crosspack can handle GUI app metadata too

Upstream source
- GitHub releases: usebruno/bruno
- Source config: `registry/sources/bruno.toml`

Version/update strategy
- automated from GitHub releases with `tag_prefix = "v"`

Metadata requirements
- 6 targets including arm64 Windows
- AppImage, dmg, and exe assets
- GUI metadata plus binary paths

Platform caveats
- GUI packaging and OS-specific install behavior raise day-one support complexity

Why later
- strong package, but GUI-heavy packages are better after core CLI trust is established

#### 13) dbeaver
Why it matters
- recognizable database GUI
- useful for proving broader package scope

Upstream source
- GitHub releases: dbeaver/dbeaver
- Source config: `registry/sources/dbeaver.toml`

Version/update strategy
- automated from GitHub releases

Metadata requirements
- 6 targets
- mixed asset kinds across tar.gz/zip/direct binary installers
- GUI metadata

Platform caveats
- heavier platform-specific variation than the day-one CLI set

Why later
- valuable expansion package, but higher maintenance surface

#### 14) beekeeper-studio
Why it matters
- another strong database GUI option
- good for modern developer GUI coverage

Upstream source
- GitHub releases: beekeeper-studio/beekeeper-studio
- Source config: `registry/sources/beekeeper-studio.toml`

Version/update strategy
- automated from GitHub releases with `tag_prefix = "v"`

Metadata requirements
- 5 targets
- AppImage/dmg/portable exe style assets
- GUI metadata

Platform caveats
- GUI complexity similar to Bruno/DBeaver

Why later
- compelling package, but not needed for day-one trust demo

#### 15) neovide
Why it matters
- attractive GUI showcase for editor users
- can demonstrate app-bundle handling

Upstream source
- GitHub releases: neovide/neovide
- Source config: `registry/sources/neovide.toml`

Version/update strategy
- automated from GitHub releases

Metadata requirements
- 4 targets only
- tar.gz + dmg + zip/exe patterns
- GUI metadata

Platform caveats
- weaker target coverage than the top CLI choices

Why later
- more niche and less symmetric cross-platform coverage

## Packages I would not prioritize for day one

- insomnia
  - useful, but GUI-first and only 4 configured targets
- redisinsight
  - valuable later for database workflows, but GUI-heavy and not central to the first trust demo

## Metadata sourcing pattern to standardize

For the recommended day-one set, the common pattern is strong and repeatable:
- upstream discovery source: GitHub Releases API
- package template source-of-truth: `registry/sources/<package>.toml`
- generated outputs:
  - `packages/<package>.toml`
  - `releases/<package>/<version>.toml`
- signing flow:
  - merged docs are signed on main by `.github/workflows/sign-manifests-on-merge.yml`
- validation flow:
  - `scripts/registry-preflight.sh`
  - schema + smoke-install checks

This is exactly the kind of operational consistency that makes the registry easier to trust.

## Day-one operating recommendation

Launch with:
- crosspack
- ripgrep
- fd
- uv
- gh

Then add in this order:
- jq
- fzf
- lazygit
- bat
- delta
- starship
- bruno
- dbeaver
- beekeeper-studio
- neovide

## Key recommendation for adoption

If the goal is to make Crosspack easier to demo and easier to trust, optimize the first impression around “boringly reliable developer CLI installs,” not around breadth.

That means:
- prefer known CLI tools before GUI apps
- prefer packages with clear GitHub release assets and strong platform symmetry
- include at least two packages with richer metadata behavior (ripgrep/fd completions, uv multi-binary)
- keep Crosspack itself in the set to prove the registry can publish and trust its own flagship package

## Confidence and uncertainty

Confidence: medium-high

What is factual here
- current package/source-config inventory
- target coverage, archive kinds, binary/completion/gui metadata observed in `registry/sources/*.toml`
- current release presence in `releases/*`
- Crosspack registry structure and automation documented in repo docs and scripts

What is inferred
- relative adoption/demo value by package
- likely maintenance burden by package class
- recommended ordering for day-one vs expansion wave

What I would verify next if we wanted to tighten confidence further
- actual release cadence and asset naming stability over the last 6-12 months for the top 10 packages
- smoke-install success rate by target for the recommended day-one 5
- whether Linux target labels and actual upstream binaries should be normalized more explicitly for a few packages (for example ripgrep musl/glibc naming)
