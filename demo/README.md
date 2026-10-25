# Authored development example

`scripts/demo.py` contains a small, explicitly fictional RFP and fictional vendor text. After runtime work is authorized, running it with `--out <new-directory>` is intended to generate one native-text PDF and two JSONL corpus versions. It does not import or run the application and never overwrites existing paths. It has not run during Dirty Mode.

The first vendor revision quotes the backup requirement; certification and submission remain gaps. The second adds a certification denial, so every corpus-dependent approval must become stale even if top-k support would remain unchanged. The parser, baseline and human decisions must be exercised later to establish actual behavior. These authored inputs do not fulfill the selected plan's lawful public-PDF, human annotation, independent custody or held-out-family obligations.

## Offline CLI regression

`tests/integration/test_cli_journey.py` generates the openly inspectable authored
`cli-fixture.json` inputs in a fresh temporary directory, reusing the existing PDF
writer, then invokes the real CLI in separate
processes. It checks a persisted LangGraph interrupt, approval retry, restart,
all-row export and byte identity, corpus invalidation, historical export replay,
gap acknowledgements, conflicting request IDs and malformed PDF rejection.
The test's automated decision exercises a fixture transition; it is not human
annotation or acceptance of a real vendor claim.

Use the exact provisioned test interpreter and its dependency lock:

```text
python scripts/check_local.py --python <pinned-python> --dependency-lock <uv.lock> --suite cli --out <new-machine-local-directory>
python scripts/check_local.py --python <pinned-python> --dependency-lock <uv.lock> --suite all --out <another-new-machine-local-directory>
```

`all` covers the non-eval test suite; the independent eval owner runs `tests/eval`.
Each output retains subprocess transcripts under `pytest-tmp`, the pytest log,
JUnit counts, interpreter/dependency versions and input hashes in `receipt.json`.
An input change during execution makes the runner fail. Existing output
directories are refused so earlier evidence is preserved. A command definition
alone is not a passing receipt. No provider, hidden fixture or listener is needed.
