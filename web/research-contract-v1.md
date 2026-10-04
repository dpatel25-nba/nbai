# NBAI research board data contract v1

Identifier: `nba-research-board/v1`. Consumer: `research-board.js`;
published snapshot: `research-data.json`; page: `research.html`.
The exported `validateBoard(payload, nowMilliseconds)` is the executable contract.
All object keys below are required; unknown keys, omitted fields and wrong types
are rejected. There is no coercion, auto-filled price, or inferred probability.
Null means unavailable, not zero. This is a presentation contract, not a strategy
specification, provenance verifier, or persisted decision/settlement ledger.

## Envelope

| Key | Type / constraint |
| --- | --- |
| schemaVersion | Literal `nba-research-board/v1` |
| generatedAt | UTC timestamp or null; required for any populated history/signals |
| historicalEvidence | Array of historical summary objects, below |
| researchSignals | Array of research-only signal objects, below |
| forwardRecords | **Empty array only in v1** |

The local candidate now includes hash-linked frozen retrospective minutes evidence
in `historicalEvidence`; `research-evidence.json` contains the derived aggregate
and its original manifest/report references. Signals and forward records remain
empty. Publication must be verified separately. Synthetic fixtures live **only in tests**. The board
does not import simulator output or call a bookmaker API. Counts of validated
object shapes are not verified market observations.

Lists have at most 1,000 items. Text is nonblank, at most 2,000 characters. Numeric
values are finite numbers, never numeric strings or booleans. Timestamps are
strict UTC `YYYY-MM-DDTHH:mm:ssZ` with real calendar dates (no implicit timezone).
Snapshot generation cannot be after the display clock. IDs are unique within
each collection. Invalid records reject the **whole snapshot**, preventing
silent selective omission. Validation errors expose field paths, not raw HTML.

## Historical evidence object

Required non-null text fields: `id`, `title`, `summary`, `sourceRef`.
Source references are rendered as text, not executable URLs. Producers must
include traceable artifact/evidence IDs and limitations in the summary/reference.
History stays in the retrospective section. Do not label explored seasons as an
untouched holdout. Minutes evidence, simulations and betting validation differ.
v1 does not verify artifact hashes or historical availability.

## Research signal object

| Key | Type / meaning |
| --- | --- |
| id | Non-null stable signal ID |
| game | Non-null display matchup; authoritative game ID should be in sourceRef |
| startsAt | UTC game start or null |
| quoteId | Same-book quote/snapshot identifier or null |
| sourceRef | Evidence/artifact reference for that quote and forecast, or null |
| bookmaker | Actual bookmaker name or null; never a consensus price |
| market | `game_total` or null; broader markets require a new contract |
| side | `over`, `under`, or null |
| line | Positive finite number or null |
| price | Actual observed price object, below, or null |
| quoteAt | Actual source quote time (not retrieval time), or null |
| forecastAt | Pregame forecast generation time or null |
| modelVersion | Exact forecast model/version reference or null |
| probability | Number in [0,1], for this side/line, or null |
| uncertainty | Interval object, below, or null |
| rationale | Plain-language reasons including availability assumptions, or null |
| exclusions | Array of nonblank reason strings; retain all known exclusions |

Price object: exactly `{format, value}`. Format `american` requires an integer
value <= -100 or >= +100; `decimal` requires a finite value > 1. Show the supplied
format and value. Never assume -110, derive price from line, average books, or
treat probability as observed price.

Uncertainty object: exactly `{lower, upper, method}`, with bounds in [0,1],
lower <= supplied probability <= upper, and nonblank method text explaining
the interval and its limitations. Non-null uncertainty requires a probability.
No calibration or confidence claim is inferred. Null uncertainty stays unavailable.

When present, quoteAt <= forecastAt; both must be <= generatedAt and the
display clock and strictly < startsAt. A row can be structurally valid but
incomplete: missing quote, forecast, model, probability, interval, rationale or
source fields remains visibly unavailable and non-actionable. An empty exclusions
array means none supplied, **not independently verified**.

Same-book/game/side/line and forecast lineage must be audited upstream before
populating real records. Shape validation cannot establish source truth.
No strategy threshold, expected return, pick selection or betting action exists.

## Freshness and rendering

Quote, forecast or snapshot age **greater than 15 minutes**, or game already
started, yields a stale/non-actionable warning. This is a conservative display
policy, not an evaluated strategy or proof a quote is still executable.
Stale data remains visible with its original timestamps and warnings.
Exclusions and missing fields remain visible even when stale.

Absent/unloadable data is unavailable; malformed data is rejected; a valid empty
snapshot is explicitly empty. None yields invented lines, odds or records.
All producer strings are HTML-escaped; no links/scripts are interpreted.
The page rechecks age every minute using the same snapshot, without rewriting
timestamps or pretending a new retrieval occurred. Reload the page to fetch again.
No localStorage, network writes, cookies, settlement actions or automatic bets.

## M4 boundary / next contract

Forward record input is reserved and must remain empty. The page always reports
**0 verified forward observations** and unavailable return, calibration, drawdown
and closing-line comparison. This count means none are connected/verified here,
not proof that no records exist elsewhere.

A future, separately reviewed integration must reconcile immutable pregame
decision IDs, strategy versions, quote links, picks AND passes, pending/win/loss/
push/void outcomes, actual-price returns, and append-only correction events.
No present code persists, verifies or settles those records. Do not use the
research signal array as a forward performance ledger.

## Checks and preview

Run `node --test tests/test_research_board.cjs` from the repository root.
Tests use synthetic fixtures only and a mock fetch/root, not a real browser.
They cover valid, nullable/missing, stale, malformed, escaping, navigation,
HTTP/JSON failures and empty production payloads.

Serve the existing `web/` directory over local HTTP for a manual preview.
Opening research.html via file:// may block JSON fetch; it fails to the honest
unavailable state. In a browser, check narrow and wide layouts, keyboard focus,
skip/section navigation, disabled JavaScript, failed requests and aging quotes.
Static Node rendering checks do not establish those browser behaviors or deployment.
Independent exact-version testing and separate publication approval remain required.
