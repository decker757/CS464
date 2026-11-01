# Diagram conventions

Every diagram in `docs/diagrams/` follows these, so the set reads as one
system in the PDF.

## Files

- One diagram per file, `NN-kebab-name.mmd`, Mermaid source only. The `.mmd`
  is the source of truth; `rendered/` is generated from it.
- Line 1 is a comment block, before the diagram keyword:

  ```
  %% NN — Title in a few words
  %% Drawn from: ADR 0010, ADR 0021, D-049, docker-compose.yml,
  %%   backend/ledger_service/service/bus.py
  ```

  Name the ADRs by number and the files by repo path. If a diagram claims
  something only the code says, the file goes on this line.
- Render with `python docs/diagrams/render.py NN-name` (all of them with no
  argument). It uses `mermaid.config.json`, a white background, and writes
  `rendered/NN-name.svg` and `rendered/NN-name.png` at 1600 px wide.

## Labels

- A node: at most about **5 words**. A line break (`<br/>`) for a second
  line of detail, such as a port or a role, is fine.
- An arrow or message: at most about **8 words**.
- Use the exact names from the code: `ledger_svc`, `market.price`,
  `/ws/prices`, `LEDGER_SERVICE_KEY`, `pending_resolution`. Do not invent
  friendlier names for things the code has named.
- Readable in print: `render.py` prints the size the text would print at
  17 cm (full text width). Body text should be 6.5 pt or more at the width the
  figure is placed at; a landscape page gives 24.6 cm. Below that, cut words,
  narrow the layout or split the diagram.

## Lines

- **Solid**: exists in the code today.
- **Dashed**, and labelled `planned`: does not exist yet ([5.3] #19 deploy,
  the AWS design). In a flowchart `-.->|"planned: ..."|`.
- Dotted is reserved for one other meaning, and only where the diagram
  says so in its legend: a cross-schema id that is not a foreign key (ERDs)
  or INSERT-only access to `audit.admin_actions` (architecture). Never use it
  decoratively.
- A denied path is `--x`.

## Colour — one per service, everywhere

Paste these `classDef` lines into any flowchart that shows these things, and
use them for nothing else:

```
classDef browser  fill:#F2F2F2,stroke:#555555,color:#1A202C
classDef edge     fill:#FFF4CC,stroke:#9A7B00,color:#1A202C
classDef auth     fill:#DCEBFA,stroke:#2F6DB5,color:#1A202C
classDef market   fill:#DFF2E1,stroke:#2E8B47,color:#1A202C
classDef ledger   fill:#FCEBCF,stroke:#C27C0E,color:#1A202C
classDef audit    fill:#ECE3F7,stroke:#7048A8,color:#1A202C
classDef realtime fill:#DDF3F3,stroke:#1F8A8A,color:#1A202C
classDef pg       fill:#E6EBF2,stroke:#3B5068,color:#1A202C
classDef redis    fill:#FBE0DE,stroke:#C0392B,color:#1A202C
classDef job      fill:#FFFFFF,stroke:#4A5568,color:#1A202C,stroke-dasharray:2 2
classDef planned  fill:#FFFFFF,stroke:#888888,color:#555555,stroke-dasharray:6 4
classDef ext      fill:#FFFFFF,stroke:#4A5568,color:#1A202C
```

- `edge` is whatever sits in front of the services: nginx, CloudFront, ALB.
- `job` is a background task inside a service (the close sweeper, the
  leaderboard refresher); put it inside or beside its service's colour.
- Sequence diagrams: participants in the same left-to-right order wherever
  they appear — Browser, realtime, auth, market, ledger, Postgres, Redis —
  dropping the ones a diagram does not use. Use `box` with the service's fill
  colour, such as `box rgb(252,235,207) ledger`, only if it helps.
- ERDs: Mermaid cannot colour an entity per schema reliably, so name the
  schema in the title comment and, in the overview, prefix entities with the
  schema (`ledger_entries`, or a `%%` legend). Crow's foot notation as Mermaid
  draws it.
- State diagrams: `classDef` with the market colour for live states, grey for
  terminal states.

## Names

| Thing | Write it as |
| --- | --- |
| The five services | `auth`, `market`, `ledger`, `audit`, `realtime` (compose names). Add `:8001` or `market_svc` only where the port or the role is the point |
| The database | `Postgres`, with schema names `auth`, `market`, `ledger`, `audit` |
| The bus | `Redis` |
| The frontend | `Browser` (React SPA) |
| The proxy | `nginx` locally, `ALB` on AWS |

## Rendering gotchas (mmdc 12, Mermaid 12)

- Flowcharts are laid out by ELK. Where one edge hops another, Mermaid
  redraws a dashed or dotted edge solid, so `mermaid.config.json` turns line
  hops off. Leave it off, or "planned" lines silently become real ones.
- A bare `%%` line with nothing after it breaks the parser. Use `%% -` as a
  blank line in a comment block.
- A per-file `%%{init: ...}%%` line after the comment block is fine for
  layout (`wrappingWidth`, ELK node order, lifeline CSS, sequence widths) and
  for a larger `fontSize` where print size needs it. Colours and the font
  family stay in the shared config.
- A gantt lays itself out to the viewport width, which is why `render.py`
  passes the width when it draws the SVG as well as the PNG.

## Captions

`docs/architecture.md` gives each embedded diagram a one-sentence caption and
its "Drawn from:" line, in Ernest's style.
