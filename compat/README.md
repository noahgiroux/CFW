# CFW prepared runtime

`compat/` publishes the deterministic compatibility boundary for
Chocolatey-for-Wine (CFW). A released CFW runtime is a complete, verified
**prepared Wine prefix**. Consumers such as Cage select a compatible Wine image,
verify the released CFW artifact, seed it into an empty prefix, then perform
application-specific package orchestration.

The contract is [`contract.json`](contract.json). Producer inputs are locked in
[`runtime-inputs.json`](runtime-inputs.json). The native installer's Chocolatey,
PowerShell, .NET, and auxiliary download URLs and filenames are generated from
that lock into `installer-inputs.h`. Regenerate it with
`python3 compat/runtime_inputs.py header > compat/installer-inputs.h`. CI rejects
a stale generated header.

`bash compat/compile-installer.sh OUTPUT.exe` is the shared MinGW build recipe
for release packaging, compile checks, and Wine runtime qualification. Release
packing retains its separate strip/UPX steps. Branch pull requests run the
contract checks once; pushes to `main` and manual runs remain supported.

## Consumer boundary

CFW owns the prepared prefix’s Windows compatibility state:

- .NET, CLR, registry policy, and Wine compatibility state;
- PowerShell 7, the Windows PowerShell/Synchro x64 and x86 shims;
- the root PowerShell loader, CFW profile fragments, and compatibility adapters;
- canonical Chocolatey bootstrap, external-host policy, and package execution
  surface.

The callable Chocolatey interface is
`C:\ProgramData\chocolatey\choco.exe`. The standard
`C:\ProgramData\chocolatey\bin\choco.exe` shim remains present for layout
compatibility, but deterministic Wine consumers must call the real root
executable because the shimgen child-process hop is not reliable under Wine.
Release manifests expose this root executable as the authoritative consumer
interface.

A consumer owns only:

- selecting a compatible **digest-pinned** Wine image;
- verifying the detached CFW manifest, evidence, and prefix archive hashes;
- replacing a fresh prefix with the verified prepared prefix before application
  modules run;
- its application recipes, package selection, package lifecycle checks, and
  final OCI/bundle artifact.

Consumers must not reconstruct WMF, GAC, CLR, Synchro, registry, or CFW profile
state. They must not merge the archive into a mutated prefix or replace CFW’s
root `profile.ps1`.

## Runtime artifact and evidence

`compat/build-runtime.sh` produces these files for each proven Wine image:

```text
cfw-runtime-prefix-wine-<version>.tar.gz
cfw-runtime-prefix-wine-<version>.tar.gz.sha256
cfw-runtime-evidence-wine-<version>.json
cfw-runtime-manifest-wine-<version>.json
logs/
```

The detached manifest binds the archive to:

- the CFW source revision and `runtime-inputs.json` digest;
- the exact Wine OCI image digest and observed Wine version;
- the `runtime.json` hash and the exact behavioral proof inventory required by
  `compat/contract.json`;
- producer-declared consumer interfaces, including canonical Chocolatey paths
  plus direct-`wine` query and `wineconsole` package-operation launchers
  and the post-bootstrap runtime environment.

The runtime build is valid only when all behavioral proofs pass:

1. a contract-selected, bounded `wine --version` probe observes exactly the declared Wine candidate and settles against
   the digest-pinned producer image;
2. the fresh win64 prefix initializes and the CFW native bootstrap completes;
3. every Wine path conversion and pre-PowerShell Wine policy command runs under its own timeout, settles independently, and records command/settlement evidence; the policy mirrors CFW's maintained `pwsh.exe` overrides (`amsi=""`, `dwmapi=""`, and `rpcrt4=native,builtin`);
4. `pwsh.exe` receives a real `CONOUT$` through Wine 11's supported
   `wineconsole <command>` interface,
   emits script entry, reports the exact locked version, and creates matching
   filesystem evidence and a sentinel;
5. the prepared-runtime PowerShell finalizer completes and creates its sentinel;
6. both Synchro wrappers load the composed CFW profile and create independent
   x64/x86 filesystem sentinels;
7. before Chocolatey’s first process starts, CFW atomically seeds the locked
   Chocolatey 2.7.4 default configuration with the in-process
   `powershellHost` explicitly disabled, then verifies Chocolatey reports that
   persisted status;
8. canonical Chocolatey emits one exact observed version equal to the locked version;
9. a CFW-controlled local package installs and uninstalls, creating both
   lifecycle sentinels;
10. Wine settles after every critical execution boundary.

Phase 1 targets Wine 11 only. Wine 9 and 10 are reintroduced after Wine 11
passes and the first immutable prepared runtime is published.

A tagged `cfw-runtime-v*` GitHub Actions run publishes only the assets produced
by successful matrix jobs. Prefix archives normalize member order, ownership,
timestamps, PAX metadata, permissions, and gzip headers from the exact source
revision so equivalent prepared prefixes produce byte-identical archives.
Short-lived CI artifacts remain diagnostics only; they are not the consumer
interface.

`build-runtime.sh` collects the observations, then passes named return codes and
one probe directory to `runtime_evidence.py`. The writer rejects missing, duplicate, or
unknown return codes and checks the exact persisted proof bytes.
Its manifest command writes the checksum sidecar and manifest from one archive
hash and binds the evidence to the authoritative contract. The public evidence and manifest schemas and required proof inventory
remain unchanged.

## Profile composition

The prepared runtime owns:

```text
C:\Program Files\PowerShell\7\profile.ps1
C:\ProgramData\Chocolatey-for-wine\profile.d\10-runtime-contract.ps1
C:\ProgramData\Chocolatey-for-wine\profile.d\20-chocolatey.ps1
C:\ProgramData\Chocolatey-for-wine\profile.d\30-cfw-winetricks.ps1
C:\ProgramData\Chocolatey-for-wine\profile.d\40-cfw-command-adapters.ps1
```

The fragment inventory and order come from `contract.json` through
`profile-contract.ps1`. Finalization checks that the source and installed files
match that inventory exactly; empty, duplicate, unordered, unsafe, missing, or
extra fragments fail qualification. The loader preserves the installer-generated
legacy profile, then loads CFW fragments lexically. A consumer may add application-specific fragments only to:

```text
C:\ProgramData\Chocolatey-for-wine\application-profile.d
```

That directory is optional. It is an extension point, not permission to replace
the CFW loader or fragments.

## Container finalizer

[`container-finalizer.c`](container-finalizer.c) remains a bounded internal
Chocolatey promotion/recovery utility. It validates an already-provided external
layer and atomically promotes canonical Chocolatey state. It does not define the
prepared-runtime interface and must not be used by consumers as a substitute for
the released prefix artifact.

The recovery utility and installer share bounded path validation, atomic
Chocolatey bin promotion, and registry/process environment updates in
`native-common.h`. The installer still installs CLR prerequisites and requires a
fresh canonical destination; the utility still resumes staging or reconciles an
existing installation. Commands use bounded Windows argument quoting, including
cache and installation directories containing spaces.

## Legacy desktop installer

The historical interactive installer remains supported for existing users. Its
layout and implementation details are not the contract for deterministic
consumers. CFW may refactor its legacy implementation internally without
expanding Cage’s Windows compatibility surface.
