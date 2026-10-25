# Offline development verification

These checks use the bundled project-authored arithmetic packet and fictional
source/evidence. They do not establish public-PDF corpus rights, human gold,
independent custody, hidden-family quality or live model performance.

With the frozen test dependencies installed and `PYTHONPATH` set to this checkout's
`src`, run:

```powershell
python scripts/eval_public_dev.py --out <new-receipt.json>
python -m pytest -p no:cacheprovider tests/eval --basetemp <new-parent-existing-test-directory>
python scripts/docker_sqlite_e2e.py --root <new-m3-prefixed-directory>
```

The arithmetic runner accepts no input-location argument: it opens only
`eval/hand-oracle.v1.json`. Its receipt pins the fixture, scorer and harness bytes
and Git HEAD. During construction, uncommitted harness bytes are identified by
their digest; final evidence must reference the committed candidate. Missing
B0b/F1/F2 arms remain unmeasured. The public scorer rejects non-development
splits; no hidden fixtures, labels, seeds or evaluation vault are discovered.

The storage journey starts three independent Python processes. It creates a real
SQLite domain ledger and LangGraph checkpoint, commits a human gap decision before
exit, resumes the persisted interrupt after reopening, verifies idempotent resume,
exports both rows including the unresolved row, retries across another process
with identical artifact bytes, and replaces the evidence corpus. Every row must
then be stale and an old-generation export must fail. The historical bundle's
bytes must remain intact. This is synthetic integration evidence only; it does
not stand in for the real PDF CLI journey or model evaluation.

Runtime outputs belong outside OneDrive. Use a fresh directory for each run;
these runners do not remove previous results. A pytest basetemp directory must
also be new, with an existing parent, to preserve prior artifacts.

## Docker execution boundary

The same journey runs in a packaged Python image with immutable dependencies.
PB-M3-R03 Addition 005 assigns M3 the corrected block 22000–22099 and permits the
RV2 lane after a successful M3 claim/check. A script or syntax check is not a
container run. Record Docker as `not_run` until execution evidence exists.

`eval/docker-requirements.txt` is the byte-identical, hash-checked D8 export
(SHA256 `0cffb72f5488d245184ff5e5170e4b866e8b26453f8baa7a8009fcbebf960f9f`),
derived from lock SHA256
`942b9926263a3de2cc8ea63914a73e209778206c828b0010f8b192b6b09ed90f`.
`Dockerfile.e2e` verifies that digest before installing with `--require-hashes`.
It imports this candidate's source directly; no live provider SDK is installed.

After the hold is explicitly lifted, build from a full committed candidate using
`scripts/docker_build_e2e.py --candidate <full-commit> --base-image
python@sha256:<verified-base-digest> --owner <owner-label-from-claim>`. The builder requires an
immutable base digest and creates a tar context solely from allowlisted committed
source/templates, the journey script, requirements and Dockerfile. It does not
send the worktree's venv, scratch state, untracked files or credential files to
Docker. Record the resulting image ID and exact base digest as evidence.

After an exact local image is built, invoke
`scripts/docker_run_e2e.ps1` with the image's `sha256:` ID, canonical `ports.py`,
your callsign, a fresh `r03-rv2-*` instance name and a new receipt path. It requires
a successful m3 claim/check before creation, labels its m3-prefixed container and
volume, publishes no port, runs with networking disabled and a read-only root,
and preserves its stopped container and generated state. The reserved 22000
port is not a listener; SQLite itself requires no host network service.

The run wrapper neither builds/pulls images nor cleans resources. The operator must
freeze the base-image digest and dependency artifact before recording container
evidence. Image packaging and execution remain pending until those inputs and
the resource grant are available.
