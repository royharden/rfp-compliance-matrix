# Provider source admission research

Public sources inspected 2026-10-23. No SDK was installed/imported and no provider
request or credential read occurred. These observations are not runtime admission.

* [PyPI release metadata](https://pypi.org/pypi/typesafe-sdk/0.7.5/json)
  identifies `typesafe-sdk==0.7.5`, Python >=3.10, repository
  `https://github.com/typesafe-ai/typesafe-sdk-python`, and SPDX MIT.
* The public wheel `typesafe_sdk-0.7.5-py3-none-any.whl` was read as a ZIP in memory.
  Its SHA-256 matched the published metadata:
  `967a8b61494d536abb55ee3f3a2b10e81685cb6baad6ccdcab588bdc6c9b86d2`.
  `typesafe_sdk-0.7.5.dist-info/licenses/LICENSE` contains MIT permission and
  disclaimer text, with an unresolved `[year] [fullname]` copyright placeholder.
  This limitation is preserved; transitive license admission is still pending.
* SDK source `typesafe_sdk/_core/client/sync/client.py` accepts raw dictionary
  questions, `model`, per-call `retry` and `timeout`. `RetryPolicy(max_retries=0)`
  disables hidden retries. Source `response_types.py` exposes model, complete
  answer mappings and nullable token usage. The adapter copies these distributions.
* [Python SDK guide](https://docs.typesafe.ai/sdk/python),
  [basic usage](https://docs.typesafe.ai/sdk/python/usage/basic),
  [Noul](https://docs.typesafe.ai/primitives/noul),
  [Choice](https://docs.typesafe.ai/primitives/choice), and
  [citation cookbook](https://docs.typesafe.ai/cookbooks/citation_check) were read.
  Noul is a yes probability; Choice has a registered selection, distribution and
  confidence. Citation existence remains code-owned. Cookbook scores are not
  application calibration; its historical cached model differs from this policy.
* [TypeSafe models](https://docs.typesafe.ai/models) lists exact
  `jev-1.13.0` and `jev-1.14.0`; this adapter retains the selected `jev-1.13.0`.
  Its public price listing is research context only. Explicit complete price
  provenance and authorization are still required for any future request.
* [TypeSafe legal index](https://docs.typesafe.ai/legal) identifies customer
  agreements, privacy and processing terms. They require later account-specific
  admission; a permissive SDK license does not authorize a service or transfer.
* [OpenAI Chat reference](https://developers.openai.com/api/reference/resources/chat)
  describes JSON-schema structured output, usage, finish reasons and refusals.
  [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini)
  lists `gpt-4.1-mini-2025-04-14` but explicitly marks it **deprecated**.
  It is a historical schema example, not an admitted default. F2 therefore
  requires a caller-selected dated snapshot and explicit `model_provenance`;
  admission must recheck availability/compatibility instead of choosing a moving
  alias. No availability, price or quality has been exercised.
  The transport uses Python stdlib urllib (no OpenAI SDK dependency), one fixed
  HTTPS endpoint, no redirects/retries and no configurable destination.

Future activation must verify the installed distribution and dependencies,
model/price/terms/token-bound policy, caller authorization and actual APIs. The
approval hash binds all nonsecret configuration; it records application admission,
not independent proof that a caller has organizational authority. Secrets are
environment names only: `TYPESAFE_API_KEY`, `OPENAI_API_KEY`.
