# M-01 — 24/7 Live-Pipeline-Supervisor (2026-08-30)

**Status:** implementiert · **`LiveSupervisor` fährt die echte `LivePipeline` dauerhaft.**
**READ-ONLY · keine Keys · keine Trading-/Withdraw-Rechte · keine Order** (`orders_sent`
in jedem Pfad gegen 0 asserted).

```
Kraken / Bybit  (public REST + WebSocket)
   → Live Market Data → MarketContext → MTF → Strategy → Decision
   → Dynamic Signal → Alert → Risk → Paper Position
   dauerhaft, mit Recovery über Neustart / Sleep hinweg
```

Umsetzung: `runtime/supervisor.py::LiveSupervisor` + `runtime/live_pipeline.py` +
`state/store.py` + `state/recovery.py`. Einstieg: `scripts/run_live_daemon.py`.

---

## 1. Was der Supervisor leistet — und wie

| Anforderung | Umsetzung |
|---|---|
| **dauerhaft 24/7 laufen** | `LiveSupervisor.run()` ohne Deckel; läuft bis `SIGTERM`/`SIGINT` (Cloud-Scale-Down / Ctrl-C) oder `request_stop()`. `--max-seconds` nur für Tests. |
| **WebSocket überwachen + auto-reconnect** | zwei Ebenen: (a) `_WSBase.stream()` reconnectet intern mit capped-exponential-Backoff (`ws_max_reconnects=240`); (b) `LivePipeline._ws_supervised` baut eine **frische** Verbindung auf, wenn `stream()` ganz aufgibt (`ws_restarts`-Zähler). Nach jedem Neustart deckt der REST-Poller die Lücke. |
| **stale data erkennen** | `InstrumentState.stale` (letzte M5-Bar älter als `stale_after_seconds=420`); `_rest_poll_loop` prüft alle 45 s und **backfillt** per REST; Freshness fließt in `data_confidence`. |
| **Provider-Ausfälle erkennen** | Watchdog leitet `ProviderHealth` ab: `HEALTHY` → `DEGRADED` (stale / ws_restart / feed_error) → `UNAVAILABLE` (stale **und** > 3 ws_restarts). Paper-Modus: **nur degradieren**, kein Kill-Switch. |
| **Health Status führen** | `SystemHealth` (Provider/Broker/Heartbeat/Data-Blocks) + `LivePipeline.health_report()` (typisiert: `PipelineHealth` / `InstrumentHealth`). `LiveSupervisor.status()` = ein JSON-Dict. |
| **Fehler isolieren** | `EventBus(raise_on_handler_error=False)`; `LivePipeline._feed` in `try/except` je Instrument (`feed_errors`-Zähler, Daemon läuft weiter); WS-/Poll-/Watchdog-/Snapshot-Task jeweils gekapselt; die Pipeline-Task wird bei Crash vom Supervisor neu gestartet. |
| **sauber herunterfahren** | `shutdown()`: Pipeline stoppen → **finalen Snapshot** schreiben → `ShutdownRequested` publizieren → `orders_sent == 0` asserten → Exit 0. |
| **nach Restart sauber wieder anlaufen** | `_recover()` lädt den Snapshot, `LivePipeline.restore()` seedet `_last_open` / `_fed_opens` / `_last_fed_cutoff` / Zähler und **hängt offene Paper-Positionen wieder ein** (`ContinuousEvaluator.restore_positions` → der Engine schreibt sie **fort** statt sie neu zu öffnen). Dann `warmup(preserve_last_open=True)` (füllt Puffer + höhere TFs, aber **nur bis zum Recovery-Stand** — die Lücke bleibt für den Backfill), `_backfill_gap()` (jede fehlende M5-Bar einzeln durch die volle Pipeline), Prime. |
| **keine doppelten Events** | (a) `_fed_opens`-Set je Instrument (verarbeitete `open_time` verworfen, aus dem Snapshot geseedet); (b) `_last_fed_cutoff` je Instrument — **genau ein `feed()`/`DecisionMade` je cutoff**: der Prime-Durchlauf nach Recovery liegt oft auf demselben cutoff wie die letzte gebackfillte Bar und wird dann übersprungen. |
| **keine doppelten Paper-Positionen** | Restore der offenen Positionen (nur nicht-terminale) in `engine._open` — der Engine sieht die Position und öffnet sie nicht erneut. |
| **keine Daten verlieren (soweit Backfill möglich)** | `LivePipeline.backfill()` zieht M5-Bars per REST nach (deduped). `state.recovery.clamp_backfill_start` begrenzt auf ~700 M5-Bars (Kraken ~720 / Bybit ~1000) — größere Lücken werden **teil**-gebackfillt + geloggt (Datenverlust dann unvermeidbar ohne tiefere Historie). |
| **Laptop Sleep / Restart → kein inkonsistenter State** | alles Wall-Clock (`datetime.now(UTC)`, kein `time.monotonic()`); Sleep ⇒ WS-Abriss ⇒ Neustart + REST-Backfill der Lücke; Restart ⇒ Snapshot-Recovery; Forming-Bar-Guard + `_fed_opens` verhindern Doppel-/Zukunfts-Bars. |
| **cloud-fähig ohne Umbau** | ein Snapshot-Pfad (`--snapshot-dir`, in der Cloud ein Volume-Mount); `SIGTERM`-Handling; strukturierte JSON-Logs; `status()` JSON-serialisierbar (Basis für einen späteren `/health`-Endpunkt); keine lokalen Annahmen; ein Prozess, ein Container. |

**Snapshot-Format** (`state.store.SnapshotStore`, atomar via `os.replace`, `schema_version`):
je Instrument `last_open`, Zähler, offene Paper-Positionen (verlustfreier Round-Trip via
`state.recovery.paper_position_to_dict`), `seen_fill_bar`, die letzten ~60 `fed_opens`.
**Nicht** im Snapshot: `SignalTracker`-Revisionshistorie + MTF-Cache — die leiten sich nach
dem Neustart aus dem Markt neu ab (konsistent, nur die Historie ist kürzer). Ein Snapshot mit
falscher `schema_version` oder kaputtem JSON wird **verworfen** (fail-safe Kaltstart), nie halb
geladen.

---

## 2. Follow-up 1 — höhere Timeframes rollierend resampeln

Vorher: `_refresh_higher` holte **M15 + H4 + D1** alle 12 M5-Bars per REST (3 Calls/h/Instrument).

Jetzt (`LivePipelineConfig`):
- **M15**: Warmup füllt es **einmalig** per REST (`force_rest=True`, ~451 Bars). Danach wird bei
  jedem 3. M5-Bar aus dem **rollierenden M5-Puffer** (`m5_store_bars=1600` ≈ 5.5 Tage) ein
  frisches M15-Segment resampelt und mit der bestehenden Historie **lückenlos verschmolzen**
  (Kontinuitäts-Check an der Merge-Grenze; passt der Schritt nicht, bleibt die REST-Historie) —
  **kein weiterer REST-Call für M15**.
- **H4** REST-Refresh alle 48 M5-Bars (= 4 h), **D1** alle 288 (= 1 Tag) — `higher_rest_every`.
- Reicht der REST-Verlauf für H4/D1 nicht, wird aus M5 abgeleitet (kein Fake).

⇒ REST-Last für höhere TFs von ~3/h auf ~0.25/h gesenkt, **M15 bleibt tief (~450 Bars)**, keine
Lücke, kein Datenverlust. Tests: `test_rolling_m15_resample_avoids_rest` (M15 bleibt ≥ 120 Bars,
exakt 15-min-Schritte, kein REST nach Warmup).

---

## 3. Follow-up 2 — Bybit Funding + Open Interest im DerivativesContext

`LivePipeline._maybe_refresh_derivatives` (nur `exchange == "bybit"`, `cfg.derivatives=True`):
- `fetch_funding` → letzte realisierte Rate (`funding_rate`, `funding_rate_as_of`).
- `fetch_open_interest` → letzter Wert (`open_interest`), Delta über das Fenster
  (`open_interest_delta_pct`).
- **Nur wenn die REST-Endpunkte valide, PIT-korrekte Daten liefern** — leere/fehlerhafte
  Antwort ⇒ `DerivativesContext` bleibt leer (kein Fake). PIT-Check im `_build_context`:
  nichts mit `as_of > cutoff`.

**Echte Endpunkte verifiziert (2026-08-30):**
`/v5/market/funding/history` → 6 Zeilen/2 Tage, letzte Rate 6.59e-05;
`/v5/market/open-interest` → 24 Zeilen/Tag (stündlich), letzter OI ~49 888 BTC.
Test: `test_derivatives_only_when_valid` (valide → gefüllt; leer → `None`).

---

## 4. Langer Live-Paper-Test (echte Feeds, 18 min, `run_live_daemon.py --max-seconds 1080`)

Bybit **und** Kraken, BTCUSDT + ETHUSDT, `--derivatives`, Research-Modus:

| | Bybit | Kraken |
|---|---|---|
| Uptime | 1085 s (= wall) | 1090 s (= wall) |
| Warmup je Symbol | `M5=401 M15=451 H4=301 D1=221` | dito |
| Confirmed WS-Bars | 3 je Symbol (22:55/23:00/23:05) | 3 je Symbol |
| `DecisionMade`-Events | 8 (2 prime + 6 WS) | 8 |
| Decision | durchgehend `NO_TRADE / SCANNING` | dito |
| Signale / Paper-Positionen | 0 / 0 | 0 / 0 |
| `feed_errors` / `dup_bars` / `qblocks` | 0 / 0 / 0 | 0 / 0 / 0 |
| `ws_restarts` / `any_stale` | 0 / False | 0 / False |
| Snapshots geschrieben | 25 (alle 45 s) | 25 |
| Provider-Health | `healthy` | `healthy` |
| SystemHealth `ok` / kill_switch | True / False | True / False |
| **`orders_sent`** (4 Prüfstellen) | **0** | **0** |

MTF: alle 4 TFs im Kontext (M5 404, M15/H4/D1 aus Warmup + rollierend). Funding/OI: mit
`--derivatives` befüllt (Bybit), Kraken hat keine linearen Perps ⇒ leer.

### Restart-/Recovery-Test (echt, Phase 1 → Sleep-Lücke → Phase 2, gleicher `--snapshot-dir`)

| Schritt | Ergebnis |
|---|---|
| Phase 1 (5 min) | 4 Decisions, letzte verarbeitete Bar 23:25→23:30, Snapshot `last_open=23:25`, `orders_sent=0` |
| Prozess-Stopp | ~8 min Lücke (Bars 23:30 wird verpasst) |
| Phase 2 Start | `Snapshot geladen: restored=True, BTCUSDT last_open=23:25, open_positions_restored=0` |
| Warmup (recovery) | `M5=400 M15=451 H4=301 D1=221` — **M15 bleibt tief** (Merge-Fix) |
| Gap-Backfill | `{BTCUSDT: {gap_bars: 1, backfilled: 1}}` — die verpasste 23:30-Bar per REST nachgezogen und **durch die volle Pipeline** gefahren (`source=backfill`, cutoff 23:35, `NO_TRADE`) |
| Prime | übersprungen (`_last_fed_cutoff` — derselbe cutoff wie die Backfill-Bar) |
| WS-Fortsetzung | nahtlos: nächste Bar 23:35→23:40 via WS (cutoff 23:40) |
| Duplikate | **0** — jeder cutoff genau 1×, `feed_errors=0`, `qblocks=0`, die Snapshot-Grenzbar (23:25) nicht erneut verarbeitet |
| Sleep während Phase 2 | die Maschine suspendierte kurz; nach dem Aufwachen erkannte der REST-Poll die Staleness und backfillte — `dup_bars`/`rest_backfills` = 4 (der `_fed_opens`-Guard verwarf jede redundante Bar), **0 doppelte Decisions** |
| `orders_sent` | **0** |

## 5. Tests

`+15` (900 grün, ruff + ruff-format + mypy --strict grün):
- `test_state.py` (8) — SnapshotStore atomar/versioniert/fail-safe · `gap_bars`/`backfillable`/
  `clamp_backfill_start` · `PaperPosition` JSON-Round-Trip.
- `test_live_supervisor.py` (6) — bounded 24/7-Lauf + sauberer Shutdown + Snapshot + WS-Restart ·
  **Recovery aus Snapshot ohne Doppel-Events** · **Gap-Backfill ohne doppelten cutoff** ·
  rollierendes M15-Resample (bleibt tief, kein REST) · Derivatives nur bei validen Daten ·
  `request_stop()` graceful.

Zusätzlich unverändert grün: die Phase-2B-`Supervisor`-Integrationstests (`test_paper_live_2b.py`)
und die Live-Pipeline-/WS-/Adapter-Tests.

---

## 6. Betrieb

```bash
# 24/7 (bis SIGTERM):
python scripts/run_live_daemon.py --exchange bybit --symbols BTCUSDT ETHUSDT \
    --derivatives --snapshot-dir /data/state

# Cloud: derselbe Befehl im Container, --snapshot-dir auf ein Volume; SIGTERM ⇒ graceful.
```

`--max-seconds` nur für begrenzte Läufe/Tests. Ein Neustart mit **gleichem** `--snapshot-dir`
nimmt den letzten Stand auf.

## 7. Offen / Backlog

- `runtime/api.py` — read-only `/health` + `/status` + `/positions` (FastAPI, **kein** Order-Pfad).
- H4/D1 ebenfalls rollierend (bräuchte einen zweiten, gröberen Bar-Puffer) — aktuell REST bei
  4h/1d-Kadenz, Aufwand/Nutzen gering.
- Kraken hat keine linearen Perps im public-Feed ⇒ Derivatives bleiben Bybit-only.
- MT5 / XAUUSD / FX — **nächster Schritt** nach diesem Block.
