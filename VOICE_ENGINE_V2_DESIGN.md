# Voice Engine v2 — kompletní přepis hlasového ovládání

**Stav:** implementováno v `secretary_clean/voice2/`. `/api/v1/voice/execute` nyní běží na v2;
kontrakt odpovědi (`VoiceExecuteResult`) je zachován a rozšířen (Android beze změny).

## Proč přepis

Slabiny v1 (viz audit): 860řádkový monolitický executor, 4 rozjeté seznamy intentů,
mrtvé `requires_confirmation`, žádná verifikace po zápisu, AI učení jen v paměti procesu,
jeden intent na větu, dva paralelní dialogové systémy.

## Architektura v2

```
voice2/
├── nlu.py       — normalizace, SEGMENTACE vícepříkazové věty, dědění kontextu, extrakce entit
├── engine.py    — pipeline: pending → korekce → resoluce (alias→parser→synonyma→AI) →
│                  sloty → validace → potvrzení (dangerous) → permission → EXEKUCE →
│                  VERIFIKACE → report → UČENÍ
├── handlers.py  — registry-driven dispatch: intent → handler(ctx, data) → HandlerResult
│                  (všech ~20 původních + nové: invoice, quote, lead, note, task.assign)
├── verify.py    — zpětné čtení zapsané entity a porovnání klíčových polí
└── profile.py   — per-user preference/statistiky (clean_voice_user_prefs)
```

## Invarianty (vynucené strukturou)

1. Alias = překlad, ne oprávnění — permission intentu se kontroluje při exekuci.
2. Dangerous intent (registry `DANGEROUS_INTENTS`) **vždy** vyžaduje potvrzení:
   `confirmed=true` v požadavku, nebo potvrzovací obrátka přes pending action („ano").
3. Každý zápis se verifikuje zpětným čtením (`data.verified`, `data.verification`).
4. Tenant izolace: vše přes repository s `company_id`; žádné raw SQL.
5. Učení je per-user (user_id) s fallbackem na tenant; AI-rozpoznané fráze se
   ukládají jako trvalé aliasy (`source="ai_learning"`) — přežijí restart.
6. Learning event za každou resoluci (append-only audit), nikdy nesmí shodit příkaz.

## Vícepříkazová věta

`nlu.segment()` dělí na klauzule (spojky „a pak/potom/a taky/a" + nové sloveso;
výčty hodnot nedělí). Kontext (osoba/klient/datum) se dědí do dalších segmentů.
Segmenty se vykonávají sekvenčně; při chybějícím slotu/potvrzení se zbytek fronty
uloží do pending action (`collected_data._queue`) a pokračuje po odpovědi.
Odpověď: `data.commands = [ {intent, status, executed, verified, message, entity_id} ]`,
`message` = spojené hlášky.

## Učení (proces zaručující zpřesňování)

| Signál | Akce |
|---|---|
| AI fallback rozpozná frázi | trvalý per-user alias `ai_learning` (ACTIVE) |
| Uživatel naučí frázi dialogem | alias `user_learning` (ACTIVE/PENDING dle implementace) |
| PENDING alias + modul doimplementován | aktivace při startu / endpointem |
| Každá resoluce | learning event (zdroj, intent, executed) |
| Užití aliasu | `use_count`, `last_used_at` (touch) |
| Preference uživatele | `clean_voice_user_prefs` (JSONB) |

## Railway DB

Migrace čistě aditivní (vzor `db/migration.py`): nová tabulka `clean_voice_user_prefs`.
Existující voice tabulky beze změny. Vše přes validované repository operace →
stejná logika a audit jako UI cesta; verifikace čte tatáž data zpět.

## Testy

Stávající suite zůstává kontraktem (upraveny jen testy počítající s neexistencí
potvrzování u dangerous intentů). Nové: multi-command, potvrzování, verifikace,
persistence AI aliasů, nové intenty.
