# Windows packaging review notes

This layer is stacked on the desktop lifecycle shell and adds only:

- an NSIS x64 installer;
- a checksum-pinned CPython 3.12.10 embedded runtime;
- the existing production frontend and the upstream hash-locked base Python
  dependencies;
- the verified native DLL subset required for WeasyPrint PDF output;
- Electron `safeStorage` for LLM, Tushare, and QVeris credentials.

## Deliberate exclusions

- No updater or release feed is included.
- No optional IM/channel extra is installed.
- Personal WeChat/Weixin QR pairing is not included.
- No broker-specific optional dependency is installed.
- The review workflow produces an unsigned artifact only; it does not publish a release.

Users can install an optional adapter later from a source/developer environment.
The desktop runtime does not expose a package installer UI in this change.

## Build

From a complete checkout of the current upstream source:

```powershell
cd frontend
npm ci
npm run build

cd ..\desktop\electron
npm ci
npm run runtime:win -- -Clean
npm run smoke:lifecycle
npm run installer:win
```

The installer and checksum are written under `desktop/electron/release/`.
Python dependencies are installed from
`desktop/electron/requirements-windows-lock.txt` with `--require-hashes`; the
checked-out Vibe-Trading package is then installed with `--no-deps` so
packaging cannot silently resolve versions outside that lock.

The repository-root lock is generated for the upstream Linux environment and
contains platform-specific dependencies such as `uvloop`. It cannot be reused
for the Windows runtime. Regenerate the Windows lock only on Windows with
Python 3.12, from the base requirements (not an optional IM extra):

```powershell
python -m pip install pip-tools
python -m piptools compile --generate-hashes --allow-unsafe --resolver=backtracking --output-file=desktop/electron/requirements-windows-lock.txt agent/requirements.txt
```

## Credential boundary

The renderer can request a write only for an allowlisted credential name.
Encryption and decryption happen in the Electron main process. On Windows,
Electron `safeStorage` uses the operating-system encryption facility for the
current user. Decrypted values are injected only into the child backend
environment and are not returned to the renderer.

On first startup, supported secrets in `~/.vibe-trading/.env` and the QVeris
API key in `~/.vibe-trading/qveris.json` are migrated into encrypted storage.
The plaintext fields are then removed. Backend settings writes in desktop
secure mode explicitly blank known credential fields instead of writing the
injected values back to dotenv.

## Unsigned limitation

Local and pull-request artifacts are unsigned. Windows SmartScreen may warn
when they are launched. Code signing and release ownership remain with the
community publisher unless HKUDS explicitly takes them over later.

## Dependency-audit note

`npm audit --omit=dev` reports zero production dependency vulnerabilities.
Electron Builder is a build-time-only development dependency and its current
transitive tree reports high-severity audit findings in glob/minimatch-related
tooling. Those packages are not copied into the packaged application, but the
review workflow still treats the lockfile as trusted build input. This should
be re-audited whenever Electron Builder publishes a repaired dependency tree.

## Local validation record

Windows host validation on 2026-07-28:

- [x] all 183 installed third-party Python distributions exactly match the
  committed Windows lock; the checked-out `vibe-trading-ai` package is the only
  additional distribution;
- [x] embedded Python reports version 3.12.10;
- [x] backend, CLI, and WeasyPrint PDF imports succeed;
- [x] DingTalk, Discord, Telegram, Neonize/WeChat, and QR-code modules are
  absent;
- [x] authenticated random-port startup and graceful shutdown succeed with no
  residual embedded Python process;
- [x] the packaged application loads the frontend and backend from an isolated
  empty Windows user profile and closes its owned process tree cleanly;
- [x] the generated installer is reported as `NotSigned` by Windows
  Authenticode inspection.

The assembled backend is 801.4 MiB before installer compression. A clean
Windows VM re-run remains a release/review gate; this host record is not a
substitute for that independent environment check.
