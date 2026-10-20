# Chocolatey-for-Wine integration fork

This fork adapts the upstream Chocolatey-for-Wine project for deterministic
Wine application builders, particularly [Cage](https://github.com/Pelagians/Cage).
It preserves the upstream-derived interactive installer and adds a reproducible
prepared-prefix runtime contract for container consumers.

## Project direction

CFW owns the compatibility stack that cannot be separated cleanly from Wine:

1. a fresh win64 Wine prefix;
2. the inherited .NET and CLR policy;
3. the Windows PowerShell runtime used by Chocolatey packages;
4. pinned Synchro `powershell.exe` wrappers;
5. canonical Chocolatey state;
6. CFW registry policy, command adapters, and application workarounds.

These parts are built and proved together once. Consumers do not reconstruct
WMF servicing payloads, the .NET GAC, native CLR loaders, or wrapper state during
every application build.

The machine-readable contract is [`compat/contract.json`](compat/contract.json).

## Integrated canonical upstream

This branch selectively integrates canonical upstream release `v0.5c.765` at
commit `71bf92916b8d259458017a583a37dfde330b241e`. Its release archive and the
PowerShell 7.6.5 MSI are checksum-pinned in
[`compat/runtime-inputs.json`](compat/runtime-inputs.json). Chocolatey is
independently pinned to 2.7.4. The small
WebView direct-composition workaround and version-agnostic, exact-singleton
PowerShell MSI cache discovery are ported into the locally hardened installer.

The release's broad winetricks changes and Wine `combase`/`wintypes` patch assets
are intentionally deferred; the upstream bootstrap reorder is also not adopted.
Those changes include mutable downloads, fixed byte-range assumptions, and opaque
or source/archive-skewed binaries that require separate
input pinning and Wine 11 rebuild proofs. The local fail-closed process
orchestration, offline cache enforcement, runtime evidence, and producer/consumer
boundary remain authoritative. Selective integration does not by itself claim a
new prepared runtime is published; that requires the complete Wine 11 behavioral
build and immutable tag workflow.

## Chocolatey 2.7.4 candidate

The Chocolatey package URL, SHA-256, installer download, saved-cache filename,
runtime identity, and capability contract now select 2.7.4. Both the community
feed and the upstream installer's package endpoint returned the same package
SHA-256: `aec2dbd6ce91b7378eb4a0c21524e748d30332ea856e898404999f77328bd504`.
Its package metadata identifies `chocolatey` version `2.7.4`, and its canonical
executable remains `tools/chocolateyInstall/choco.exe`.

Upstream release `v0.5e.765` advertises 2.7.4. Inspection of its checksum-verified
installer confirms the 2.7.4 download target even though that tag's `installer.c`
still names 2.6.0. This candidate updates the locally hardened installer directly;
the upstream bootstrap archive stays pinned to `v0.5c.765`.

The default configuration was checked against Chocolatey CLI tag `2.7.4`, source
revision `37541d3b7e7b3079d69e70a0bfd70f302b808e4d`; CFW retains its explicit
external PowerShell host policy. This is a candidate until the Wine 11 producer
CI passes the complete PowerShell, Synchro, Chocolatey version, feature-policy,
and local install/uninstall proofs. Publishing and consumer adoption require a
new immutable runtime release; existing releases are unchanged.

## Prepared runtime artifact

The prepared prefix is produced inside the same universal Cage image that will
run it. The producer workflow resolves the Wine image to an immutable GHCR
digest, requires the LinuxServer `/init` entrypoint and
`cage.selkies-wayland/v1` session label, and submits the finite build through
Cage's native s6 task channel. The task runs as `abc` with the GitHub runner's
`PUID`/`PGID`; it does not override `/init` or migrate a prefix produced by a
different image. Manual requalification accepts an exact
`ghcr.io/pelagians/cage-wine@sha256:...` input so a candidate can be proven
before its mutable version tag advances.

`compat/build-runtime.sh` builds a reusable prefix foundation from verified
inputs. It requires all of the following before producing an artifact:

- the full CFW installer exits successfully and Wine settles;
- the native container finalizer selects Microsoft .NET's `mscoree.dll`, and
  the resulting Wine DLL override is verified before Chocolatey starts;
- the source-controlled pre-PowerShell Wine policy, including CFW's maintained `pwsh.exe` RPC override, is applied and independently evidenced;
- Windows `pwsh.exe` runs through Wine's X-backed user console, emits script
  entry, reports the exact locked version, and creates matching filesystem
  evidence and a sentinel;
- the CFW prepared-runtime PowerShell finalizer completes and creates its
  sentinel;
- pinned Synchro v4.2.0 x64 and x86 wrappers each create a filesystem side effect;
- the runtime interface separates direct-`wine` Chocolatey queries from
  `wineconsole` package operations so Synchro receives a real console;
- canonical Chocolatey reports its version and its in-process PowerShell host is
  disabled and verified;
- a CFW-controlled local Chocolatey package installs and uninstalls cleanly;
- Wine settles after every critical runtime proof.

A successful build produces:

```text
cfw-runtime-prefix-wine-<version>.tar.gz
cfw-runtime-prefix-wine-<version>.tar.gz.sha256
cfw-runtime-evidence-wine-<version>.json
cfw-runtime-manifest-wine-<version>.json
logs/
```

`runtime.json` records the exact Wine image digest, observed Wine version, and
producer session contract, plus the CFW source revision, compatibility-contract
digest, lock-file digest, installer digest, runtime proof return codes, and sentinel
hashes. The detached manifest binds that evidence to the archive name, digest,
and byte size, the contract-authoritative behavioral proof inventory, and
producer-declared consumer interfaces (including the post-bootstrap runtime
environment). The archive is a complete prepared prefix, not a loose file
overlay. A consumer must verify the manifest, evidence, archive hash, and Wine
digest before replacing a fresh prefix.

Phase 1 currently evaluates only the published Cage Wine 11.0 runtime image.
Wine 9.0 and 10.0 return to the matrix only after Wine 11 passes every runtime
proof and produces the first immutable release.

## Deterministic consumers

Cage and similar builders should:

- download a released CFW runtime archive by immutable URL and SHA-256;
- verify `runtime.json`, the exact proof inventory and interfaces declared by
  the compatibility contract, and the exact Wine image/observed-version identity;
- replace a fresh prefix with the prepared prefix before other modules run;
- validate `choco.exe` with a real package install and uninstall lifecycle;
- keep application-specific package selection and orchestration outside CFW;
- never reproduce CFW's CLR, WMF, GAC, or Synchro installation internals.

The CFW-owned root loader preserves the installer-generated profile, then loads
CFW’s ordered fragments. Consumers may add application-specific fragments only
under `C:\ProgramData\Chocolatey-for-wine\application-profile.d`; they must not
replace CFW’s profile loader, fragments, or Synchro wrappers.

## Package-host boundary

The prepared runtime retains the legacy Windows PowerShell type dependencies
required by Chocolatey 2.6.0 while qualifying the 2.7.4 upgrade. Chocolatey discovers
its own command and rule types even when its in-process PowerShell host is
disabled. CFW therefore supplies the locked legacy type-dependency assembly set
beside `choco.exe`. This satisfies CLR type discovery without making the
in-process host part of the runtime contract.

Chocolatey packages execute through the external Windows-compatible PowerShell
surface included in the prepared runtime. Synchro is part of that artifact rather
than a separate dependency that every consumer must install.

## Native container finalizer

`CFW_CONTAINER_BUILDER=1` still enables the bounded native Chocolatey promotion
path in `compat/container-finalizer.c`. It is useful for diagnostics and for
establishing canonical Chocolatey state, but it is not the complete runtime
artifact. The complete deterministic boundary is the prepared prefix produced by
`compat/build-runtime.sh`.

Supported installer environment variables include:

- `CFW_CACHE`: Windows-form path containing `choc_install_files`;
- `CFW_OFFLINE=1`: prohibit installer download fallbacks;
- `CFW_CONTAINER_BUILDER=1`: use the native Chocolatey-only finalizer.

## Interactive desktop installer

For the inherited desktop environment:

1. Start from a fresh 64-bit Wine prefix.
2. Download and extract a release archive.
3. Run the included installer:

```bash
wine ChoCinstaller_0.5a.765.exe
```

Optional arguments:

```text
/s    retain downloaded installation files in the cache
/q    do not open the interactive PowerShell window after installation
```

The desktop path retains upstream behavior including .NET, PowerShell, ConEmu,
generated profiles, DLL policy, and compatibility helpers. The prepared runtime
builder wraps that behavior in strict input verification and runtime proofs.

## PowerShell wrappers

`mainv1.c` and the generated historical wrapper binaries remain for compatibility
with the desktop installer. Deterministic runtime artifacts replace the public
x64 and x86 wrapper paths with pinned Synchro v4.2.0 binaries.

The legacy wrapper source is still tested to ensure process-creation failures,
wait failures, and child exit codes are propagated rather than reported as
success.

## Compatibility pack

The additive pack contains:

- `compat/contract.json`: artifact, ownership, and consumer requirements;
- `compat/profile.d/20-chocolatey.ps1`: Chocolatey profile module import;
- `compat/profile.d/30-cfw-winetricks.ps1`: optional CFW winetricks entrypoint;
- `compat/profile.d/40-cfw-command-adapters.ps1`: ordered adapter loader.

The remaining monolithic functions in `choc_install.ps1` can still be migrated
into named assets over time:

```text
compat/registry/
compat/profile.d/
compat/command-adapters/
compat/files/
compat/app-fixes/
```

Those refactors improve CFW itself; they do not expand Cage's module surface.

## Constraints

- Runtime artifacts are built from fresh win64 prefixes.
- `WINEARCH=win32` is not supported by the inherited installer.
- Do not merge a prepared artifact into a mutated prefix.
- Do not combine the inherited .NET installation with unrelated .NET verbs
  without proving the resulting runtime.
- In-place upgrades from older CFW layouts are not yet supported.
- Installer exit code or file presence alone is never sufficient readiness proof.

## Development

Run contract tests with:

```bash
python -m unittest tests.test_layer_contract
```

The runtime matrix is defined in:

```text
.github/workflows/build-container-runtime.yml
```

The upstream-derived C sources retain their original build instructions. Release
archives must contain the native installer and its expected data files, including
`choc_install.ps1`, `c_drive.7z`, `7z.exe`, and `7z.dll`.

## Origin

This repository is derived from:

- `PietJankbal/Chocolatey-for-wine`
- `Twig6943/Chocolatey-for-wine`

The fork-specific changes focus on deterministic inputs, runtime evidence,
container-safe Chocolatey promotion, and a reusable prepared-prefix boundary.
