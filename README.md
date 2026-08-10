# rdf2ontology

Turn an RDF/OWL ontology into a **Fabric IQ Ontology** item folder that
[fabric-cicd](https://microsoft.github.io/fabric-cicd/latest/) can publish.

**The RDF file is the only required input.** No YAML config, no CLI flags - `build --input
your.ttl --output build` works on its own. When you do want a config, `rdf2ontology/config/
defaults.yaml` is a single **generic, reusable** file - no ontology-specific content - that you can
point `--config` at for *any* RDF file. `--config` is repeatable: layer an optional, per-ontology
file on top of it only when you need to override one of the tool's inferences for one specific
ontology (rename something, pin a real lakehouse id, add a time-series binding RDF can't express).

```
your_ontology.ttl ──lint──► IR ──validate──► YourOntology.Ontology/ ──deploy──► Fabric
    (required)                                     .platform
        +                                           definition.json
  defaults.yaml                                    EntityTypes/{id}/definition.json
  (optional, generic,                              EntityTypes/{id}/DataBindings/{guid}.json
   reusable as-is)                                 RelationshipTypes/{id}/definition.json
        +                                          RelationshipTypes/{id}/Contextualizations/{guid}.json
  your_ontology.overrides.yaml
  (optional, per-ontology)
```

Design notes and rationale live in
[../.github/plans/rdf-to-fabric-ontology-cli.md](../.github/plans/rdf-to-fabric-ontology-cli.md).

---

## Install

All commands in this document are run from the **repository root**.

```bash
python3 -m pip install -r requirements.txt
```

`fabric-cicd` and `azure-identity` are only needed for `deploy`; `lint`, `build`, `validate` and
`diff` run fully offline.

---

## Quick start

```bash
# with only the RDF file, nothing else
python3 -m rdf2ontology lint  --input ontology-items/rdf/customer_address_minimal.ttl
python3 -m rdf2ontology build --input ontology-items/rdf/customer_address_minimal.ttl --output build

# the generic config: same file, works unmodified on any RDF you throw at it
python3 -m rdf2ontology build --input ontology-items/rdf/customer_address_minimal.ttl --output build \
  --config rdf2ontology/config/defaults.yaml

# layer a per-ontology overrides file on top when you need one (--config is repeatable)
python3 -m rdf2ontology build --input ontology-items/rdf/customer_address_minimal.ttl --output build \
  --config rdf2ontology/config/defaults.yaml \
  --config rdf2ontology/config/customer_address.overrides.yaml

# supply real deployment ids once you have them (still no config file needed)
python3 -m rdf2ontology build --input ontology-items/rdf/customer_address_minimal.ttl --output build \
  --workspace-id b37a40dd-a14d-4fef-a91d-5567bc03347a --lakehouse-id 04c15f69-c177-4ff8-b60d-de6e0bf78172

# publish
az login
python3 -m rdf2ontology deploy --item-dir build --workspace-id b37a40dd-a14d-4fef-a91d-5567bc03347a --environment dev --dry-run
```

See [Configuration](#configuration) below for the config-file workflow (renames, per-entity keys,
time-series bindings, multiple lakehouses, environment-specific `parameter.yml`).

---

## Commands

### `lint` — step one, always

Runs against the raw graph before any mapping. `build` runs it too and **aborts on any error**;
there is no `--skip-lint`, only per-rule `--ignore`, which is recorded in the report. Most defects
(illegal names, duplicate names, class/property punning, a source table with no schema) are
**warnings** — the tool resolves them deterministically and tells you what it did. Only a genuine
parse failure or a property declared with two conflicting ranges are hard errors.

```bash
python3 -m rdf2ontology lint --input ontology-items/rdf/customer_address.ttl \
                             --config rdf2ontology/config/defaults.yaml

# machine-readable, for CI
python3 -m rdf2ontology lint --input my.ttl --format json > lint.json

# fail the pipeline on warnings too
python3 -m rdf2ontology lint --input my.ttl --fail-on warning

# silence a rule you have consciously accepted
python3 -m rdf2ontology lint --input my.ttl --ignore L-ORPHAN --ignore L-NAME-LENGTH
```

| Option | Meaning |
|---|---|
| `--input` | RDF file — Turtle, RDF/XML, N-Triples, JSON-LD, TriG (detected by extension) |
| `--config` | YAML config; repeatable; entirely optional (see [Configuration](#configuration)) |
| `--format text\|json` | Output shape |
| `--fail-on error\|warning` | Exit non-zero threshold (default `error`) |
| `--ignore RULE` | Repeatable; also settable as `lint.ignore` in the config |
| `--strict` | Promote every warning to an error |

### `build` — lint, map, validate, emit

```bash
# nothing but the RDF file
python3 -m rdf2ontology build --input ontology-items/rdf/customer_address.ttl --output build

# generic defaults + a per-ontology overrides file, plus real deployment ids
python3 -m rdf2ontology build \
  --input ontology-items/rdf/customer_address.ttl \
  --config rdf2ontology/config/defaults.yaml \
  --config rdf2ontology/config/customer_address.overrides.yaml \
  --output ontology-ci-cd \
  --name Customer_Address \
  --workspace-id <guid> --lakehouse-id <guid> \
  --json-report build-report.json
```

| Option | Meaning |
|---|---|
| `--output` | Folder that will contain `<Name>.Ontology/` (this is fabric-cicd's `repository_directory`) |
| `--name` | Display name; defaults to `ontology.displayName`, else the RDF's `dct:title`/`rdfs:label`, else the file stem |
| `--workspace-id` | Fabric workspace id, used to fill in every otherwise-unresolved binding |
| `--lakehouse-id` | Lakehouse item id; bare `GUID` applies to every unresolved lakehouse, or scope it with `name=GUID` (repeatable) |
| `--schema` | Default lakehouse schema for a `sourceTable` with no schema prefix (default `dbo`) |
| `--id-map` | Path to the persisted name → id map (default `<output>/<Name>.id-map.json`) |
| `--allow-new-ids` | Permit minting ids for concepts absent from an existing map |
| `--check-only` | Run everything, write nothing |
| `--json-report` | Write the full build report as JSON |
| `--verbose` | Include `info` diagnostics |

Sample output for the RDF-only run (no config, no flags):

```
== Ontology 'CustomerAddressOntology' ==
  entity types      : 23 (2 bound, 21 unbound)
  relationship types: 29 (10 contextualized)
  properties        : 103
  keyless           : CreditClass, CustomerStatus, ...
  bindings still missing (backlog):
    - City
    - Contact
    ...
  written           : 66 file(s) -> build/CustomerAddressOntology.Ontology
  id map            : build/CustomerAddressOntology.id-map.json
```

The **backlog** section is the actionable list: every class that still needs a `sourceTable`. Any
auto-resolved name (illegal characters, a duplicate local name) is listed separately as
`auto-renamed`, with the exact substitution the tool made.

### `validate`

Two modes — an RDF source, or an already-emitted (or hand-edited) folder.

```bash
# semantic validation + a change preview, without writing anything
python3 -m rdf2ontology validate --input ontology-items/rdf/customer_address.ttl \
                                 --config rdf2ontology/config/defaults.yaml \
                                 --config rdf2ontology/config/customer_address.overrides.yaml

# structural validation of a definition folder (e.g. one fetched from Fabric)
python3 -m rdf2ontology validate --item ontology-ci-cd/Ontology_1.Ontology
```

### `diff`

Compares two definition folders. Ids are resolved to names by default, so the diff is about
*meaning*, not generated numbers.

```bash
# what changed semantically
python3 -m rdf2ontology diff --left ontology-ci-cd/Ontology_1.Ontology \
                             --right build/Customer_Address.Ontology

# restrict to a subset and ignore descriptions/synonyms
python3 -m rdf2ontology diff --left A.Ontology --right B.Ontology \
                             --only Customer,Address --ignore-enrichment

# include generated ids in the comparison
python3 -m rdf2ontology diff --left A.Ontology --right B.Ontology --with-ids
```

### `deploy`

Wraps `fabric_cicd.publish_all_items` with `item_type_in_scope=["Ontology"]`. Credentials come
from `AzureCliCredential` — nothing is read from or written to the config.

```bash
python3 -m rdf2ontology deploy --item-dir build \
  --workspace-id <guid> --environment dev --dry-run   # prints the plan only

python3 -m rdf2ontology deploy --item-dir build \
  --workspace-id <guid> --environment dev             # asks for confirmation

python3 -m rdf2ontology deploy --item-dir build \
  --workspace-id <guid> --environment dev --yes       # CI
```

`--item-dir` accepts either the repository folder or a single `<Name>.Ontology` folder.
`unpublish_all_orphan_items` is never called.

> `updateDefinition` replaces the **full** parts tree — any edit made in the Fabric portal is
> overwritten by a deploy.

**Deploy refuses to publish a placeholder GUID.** If you built without `--workspace-id` /
`--lakehouse-id` (or a config), the tree still contains `00000000-0000-0000-0000-000000000000`.
`deploy` scans for it and stops before calling fabric-cicd, unless the value is covered by a
`parameter.yml` `find_replace` entry for the target `--environment`, or you pass
`--allow-placeholders` explicitly. `--dry-run` never blocks — it just prints a note.

```bash
python3 -m rdf2ontology deploy --item-dir build --workspace-id <guid> --yes
# refusing to publish: 12 file(s) still carry the placeholder GUID 00000000-0000-0000-0000-000000000000.
#   - CustomerAddressOntology.Ontology/EntityTypes/.../DataBindings/....json
#   ...
# pass --allow-placeholders to publish anyway, rebuild with --workspace-id/--lakehouse-id, ...
```

---

## Configuration

**Entirely optional.** `build`/`lint`/`validate` work on the RDF alone. `--config` is repeatable,
and files are deep-merged in order (dicts merge key-by-key, lists concatenate, later files win on
everything else) — so you pass **one generic file** you never edit, and layer **an optional
per-ontology file** only when you need to override one of the tool's inferences. Precedence is
always **config → RDF annotation → heuristic**; ambiguity is reported, never guessed. Unknown keys
are a hard error, so typos like `entites:` fail immediately.

### The generic file — [`rdf2ontology/config/defaults.yaml`](config/defaults.yaml)

Nothing here names a specific class, property, or ontology. Point `--config` at this file for
*any* RDF input; it never needs editing per ontology.

```yaml
rdf:
  sourceTableProperty: sourceTable       # annotation local names read from the graph
  sourceColumnProperty: sourceColumn
  sourceLakehouseProperty: sourceLakehouse
  keyProperty: isKey

defaults:
  emitUnboundEntities: true        # classes with no source table still become entity types
  emitUnboundRelationships: true   # relationships with no contextualization are still emitted
  emitForeignKeyProperties: true   # object-property source columns stay queryable as scalars
  emitInverseRelationships: false
  maxPortalNameLength: 26
  unmappedRangeValueType: String

lint:
  failOn: error
```

### An optional per-ontology overrides file

Layer this on top (`--config defaults.yaml --config <ontology>.overrides.yaml`) only for the
things that are inherently specific to *this* RDF file — a workspace/lakehouse id, a renamed
relationship, a time-series binding, an `annotationNamespace`. See
[`rdf2ontology/config/customer_address.overrides.yaml`](config/customer_address.overrides.yaml)
for the worked example this repo ships (used by the golden test).

```yaml
ontology:
  displayName: Customer_Address
  logicalId: null                  # derived deterministically when null

rdf:
  annotationNamespace: "https://example.org/ontology/customer-address#"

fabric:
  workspaceId: "00000000-0000-0000-0000-000000000000"
  lakehouses:
    ontology_lakehouse:            # keyed by the sourceLakehouse annotation value
      itemId: "776e1326-329c-a517-4699-906f0781ea3b"
      defaultSchema: dbo
  environments:                    # drives parameter.yml generation
    dev:
      workspaceId: "<dev-workspace-guid>"
      lakehouses: {ontology_lakehouse: "<dev-lakehouse-guid>"}

entities:
  Customer:
    key: [CustomerKey]
    displayNameProperty: CustomerLegalName
    sourceTable: dbo.customer      # overrides the RDF annotation
  Address:
    key: [AddressKey]
    displayNameProperty: AddressDisplayText
    timeseries:                    # not expressible in RDF — config only
      - table: dbo.address_history
        timestampColumn: BSSModificationDate
        properties: [AddressLine2, CityKey]

relationships:
  hasMainAddress:
    name: hasAddress               # rename to match an already-deployed item
    linkTable: dbo.customer
    sourceKeyColumns: [CustomerKey]
    targetKeyColumns: [MainAddressKey]

overrides:
  valueTypes:                      # declared xsd:boolean, stored as 0/1 integers
    MDUOwnerFlag: BigInt
  rename: {}                       # e.g. PostalStreetNumberLastSuffix: PostalStNumLastSuffix

exclude:
  classes: []
  properties: []                   # "Customer.Email" or "Email"
```

### What happens automatically, with no config

| Defect | Auto-resolution |
|---|---|
| Illegal name (e.g. starts with a digit, has a space) | Sanitized deterministically (`9Bad` → `X9Bad`) |
| Two different IRIs share a local name | Suffixed deterministically (`Customer`, `Customer2`, ...) |
| Same IRI declared as both a class and a property | Both are emitted — Fabric keeps entity types and properties in separate namespaces |
| `sourceTable` with no schema prefix | Defaults to `dbo` (or `--schema`) |
| A relationship end (domain/range) is never declared as a class | The relationship is dropped |
| No source table on a class | Emitted as a schema-only (unbound) entity type |
| No `--workspace-id`/`--lakehouse-id` and no config | Binding emitted with a placeholder GUID; `deploy` refuses to publish it |

Every substitution is recorded — check the `auto-renamed` section of the build summary, or
`ontology.renames` / the `renames` block in `<Name>.id-map.json` and the JSON report. Two things
are **never** silently guessed: a *bound* entity type with no resolvable key (hard error, `E3`),
and a property declared with two conflicting ranges (hard error, `L-RANGE-CONFLICT`).

### Annotations read from the graph

```turtle
cao:Customer a owl:Class ;
    cao:sourceLakehouse "ontology_lakehouse" ;
    cao:sourceTable "dbo.customer" .            # schema.table

cao:CustomerKey a owl:DatatypeProperty ;
    cao:sourceColumn "CustomerKey" ;            # falls back to the local name
    rdfs:domain cao:Customer ; rdfs:range xsd:string .

cao:hasMainAddress a owl:ObjectProperty ;
    cao:sourceColumn "MainAddressKey" ;         # the FK column — drives contextualization
    rdfs:domain cao:Customer ; rdfs:range cao:Address .
```

---

## Mapping rules

### Value types

| `rdfs:range` | Fabric `valueType` |
|---|---|
| `xsd:string`, `anyURI`, `token`, `normalizedString`, `rdf:langString` | `String` |
| `xsd:boolean` | `Boolean` |
| `xsd:dateTime`, `date`, `dateTimeStamp` | `DateTime` |
| `xsd:integer`, `int`, `long`, `short`, `byte`, `nonNegativeInteger`, … | `BigInt` |
| `xsd:double`, `float`, `decimal` | `Double` |
| anything else / missing | `String` + `W2` warning |

`overrides.valueTypes` always wins. Property names are unique **across the whole ontology**, so the
same name must carry the same `valueType` everywhere (`E4`).

### Entity key (`entityIdParts`)

1. `entities.<Class>.key` in the config
2. `isKey` annotation on the property
3. `owl:Restriction` with `owl:cardinality 1` over a datatype property of the class
4. a property named `<Class>Key` or `<Class>Id`
5. nothing resolved → **error** if the class is bound, otherwise `entityIdParts: []` + `W9`

Keys must be `String` or `BigInt`.

### Display name

`entities.<Class>.displayNameProperty`, else the first `String` property ending in `DisplayText`,
`LegalName` or `Name`, else omitted.

### Relationships

Each `owl:ObjectProperty` becomes a relationship type per (domain, range) pair, after:

- **self-loops are dropped** — Fabric requires distinct ends (`hasParentCustomer`);
- **inverses are dropped** — one edge, not two (`isMainAddressOf`);
- union domains/ranges fan out and are disambiguated as `{name}{Source}{Target}`
  (e.g. `locatedInCityCustomerCity`).

A **contextualization** is emitted when both ends have a resolved key and the link table resolves
(the source entity is bound, or `relationships.<name>.linkTable` is set). The target does **not**
need to be bound.

### Foreign-key properties

RDF models a FK as a relationship, which would lose the column as a queryable scalar. With
`emitForeignKeyProperties: true` (default), an object property carrying `sourceColumn` also emits a
`String` property on each domain entity — so `Customer.CityKey` and `Address.CityKey` exist
alongside the `locatedInCity` relationship.

### Unbound entities and relationships

A class with no source table is still emitted, as an entity type with **no `DataBindings/` folder**
(not an empty folder, not a placeholder binding). Relationships touching it survive without a
contextualization. Both raise warnings so the gap stays visible, and neither blocks the build.

Set `defaults.emitUnboundEntities: false` to suppress, or prune individually with
`exclude.classes`.

---

## IDs

Entity / property / relationship ids are positive 18-digit 64-bit integers derived deterministically
(`blake2b` over `ontology|kind|<rdf-iri>`, **not** the display name); binding and contextualization
ids are `uuid5` GUIDs. Everything is persisted in `<Name>.id-map.json`.

Because ids are keyed on the underlying RDF IRI, **renaming something never changes its id** —
whether the rename came from `overrides.rename`, auto-sanitization of an illegal name, or
auto-suffixing a duplicate. Only the display name in `entityTypes.<name>` changes; the `id` value
underneath is identical to what it was before the rename.

**Commit `<Name>.id-map.json`.** It makes rebuilds byte-identical, lets a previously unbound class
gain a binding without any id churn, and lets you pin ids that are already deployed:

```jsonc
{
  "ontologyName": "Customer_Address",
  "entityTypes": {
    "Customer": {
      "id": "5202887405100123318",
      "properties": {"CustomerKey": {"id": "3127136010913050252"}},
      "bindings": {"static": "1d7a8db9-b7f3-4c9a-a0f0-56680983466e"}
    }
  }
}
```

With an existing map, `build` reuses every known id and refuses to mint new ones unless
`--allow-new-ids` is passed.

---

## Diagnostics

### Lint rules

| Rule | Severity | Meaning |
|---|---|---|
| `L-PARSE` | error | File does not parse / prefixes unresolved |
| `L-RANGE-CONFLICT` | error | One local name, two different ranges — cannot produce a valid ontology |
| `L-SRC-LAKEHOUSE` | error | `sourceLakehouse` has no `fabric.lakehouses` entry (only fires when a config names one) |
| `L-PUN` | warning | IRI declared as both a class and a property — both are emitted |
| `L-UNDECLARED` | warning | Domain / range / inverse target never declared — the property/relationship is dropped |
| `L-DOMAIN-MISSING` | warning | Property has no `rdfs:domain` — dropped |
| `L-NAME-REGEX` | warning | Name breaks `^[a-zA-Z][a-zA-Z0-9_-]{0,127}$` — auto-sanitized |
| `L-NAME-DUP` | warning | Two IRIs collapse to one local name — auto-suffixed |
| `L-SRC-TABLE-FORMAT` | warning | Source table has no schema — defaults to `dbo` |
| `L-DOMAIN-MULTI` | warning | Union domain — reports the fan-out |
| `L-RANGE-MISSING` / `L-RANGE-UNMAPPED` | warning | valueType will default to `String` |
| `L-NAME-LENGTH` | warning | Longer than the portal's 26-char guidance |
| `L-SELFLOOP` | warning | Domain and range intersect — pair will be dropped |
| `L-CLASSEXPR` | warning | `owl:intersectionOf` / `complementOf` / `oneOf` ignored |
| `L-SRC-TABLE` | warning | No source table — becomes an unbound entity type |
| `L-SRC-COLUMN` | warning | Bound class property without a source column |
| `L-SRC-COLUMN-UNSAFE` | warning | Column name would enable delta column mapping |
| `L-KEY` | warning | No key candidate resolvable |
| `L-FK-MISSING` | warning | Object property without a source column |
| `L-RESTRICTION` | warning | Restriction outside the class's domain closure |
| `L-INVERSE` | info | Inverse pair — one side will be dropped |
| `L-ORPHAN` | info | Class with no properties and no relationships |

Only `L-PARSE`, `L-RANGE-CONFLICT` and (when a config names a lakehouse) `L-SRC-LAKEHOUSE` block
`build`. Everything else is auto-resolved and reported, never silently dropped.

### Validation

`E1` name regex (an invariant — sanitization already guarantees this for RDF-derived names) ·
`E2` valueType · `E3` key existence/type/binding · `E4` name/type consistency ·
`E5` duplicate property name · `E6` distinct relationship ends · `E7` relationship name uniqueness ·
`E8` binding cardinality and TimeSeries prerequisites · `E9` timestamp column ·
`E10` id uniqueness/format · `E11` dangling reference (e.g. `displayNameProperty`) ·
`E12` contextualization keys · `E13` `KustoTable` with `NonTimeSeries` · `E14` punning (structural
folders only — the RDF path already warns via `L-PUN`) · `E15` config references an unknown concept.

`W1` name length · `W2` unmapped range · `W3` unbound entity · `W4` dropped relationship ·
`W5` missing contextualization · `W6` `Boolean` on a 0/1 column · `W7` placeholder GUID (workspace
or lakehouse id unresolved) · `W8` unsafe column name · `W9` keyless entity · `W10` a foreign key
was also emitted as a scalar property.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Validation errors |
| `2` | Config or input error |
| `3` | Deploy failure or aborted confirmation |
| `4` | Lint errors |

---

## Deployment with fabric-cicd

`build` writes `<output>/<Name>.Ontology/` plus, when `fabric.environments` is set, a
`parameter.yml` alongside it:

```yaml
find_replace:
  - find_value: 00000000-0000-0000-0000-000000000000
    replace_value: {dev: <dev-workspace-guid>}
  - find_value: 776e1326-329c-a517-4699-906f0781ea3b
    replace_value: {dev: <dev-lakehouse-guid>}
```

Point fabric-cicd at the `--output` folder:

```python
from azure.identity import AzureCliCredential
from fabric_cicd import FabricWorkspace, publish_all_items

publish_all_items(FabricWorkspace(
    workspace_id="<guid>",
    environment="dev",
    repository_directory="build",
    item_type_in_scope=["Ontology"],
    token_credential=AzureCliCredential(),
))
```

Requires Fabric IQ Ontology (preview) enabled on the tenant and at least **Contributor** on the
workspace.

---

## Tests

```bash
python3 -m pytest rdf2ontology/tests -q
```

Includes a golden comparison against `ontology-ci-cd/Ontology_1.Ontology`: the generated `Customer`
entity type, its key, display name and data binding must match the reference exactly.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `build` exits 4 without writing | A genuine parse failure, a name-range conflict, or a configured `sourceLakehouse` that doesn't exist | Fix the RDF, or `--ignore <RULE>` if you accept it |
| `E3 bound entity type has no resolvable key` | Key heuristics found nothing for a bound class | Set `entities.<Class>.key` |
| `W7` placeholder GUID | No `--workspace-id`/`--lakehouse-id` and no config | Pass the flags, fill `fabric.lakehouses`/`fabric.environments`, or accept it until deploy time |
| `E15 config references an entity type that is not in the ontology` | Typo, or the class was excluded | Match the class local name exactly |
| `IdMapError: … not in the id map` | New concept while the map is locked | Pass `--allow-new-ids` |
| `ValueError: refusing to overwrite …` | `--output` points at a non-item folder | Use an empty folder, or one containing a real `.Ontology` item |
| Many `W3` warnings | Classes have no source table (expected) | Add `sourceTable` annotations, or accept the schema-only entity types |
| `refusing to publish: N file(s) still carry the placeholder GUID` | `deploy` found `00000000-...` with no covering `parameter.yml` entry | Rebuild with `--workspace-id`/`--lakehouse-id`, add a `parameter.yml` entry for `--environment`, or pass `--allow-placeholders` |
| Auto-renamed name you don't like | Illegal or duplicate RDF local name was auto-sanitized/suffixed | Pin your preferred name via `overrides.rename` |

