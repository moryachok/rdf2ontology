# rdf2ontology YAML configuration reference

A complete, standalone guide to authoring `rdf2ontology` YAML config files: full schema, defaults,
validation rules, how multiple `--config` files are layered, and worked examples for every feature.

For a general tool overview see [../README.md](../README.md); this document focuses purely on the
config file format.

## 1. Overview

- Config is **optional**. With no `--config` flag, `rdf2ontology` runs entirely off RDF annotations
  and built-in heuristics.
- Precedence for any given value: **config file → RDF annotation → heuristic**. A value set in
  config always wins; if absent, the tool looks for an RDF annotation; if that's absent too, it
  falls back to a naming heuristic.
- Validation is **strict**: any key that isn't part of the schema below — at any nesting level —
  raises a `ConfigError` (`unknown key(s) in '<section>': ...`). Typos fail fast instead of being
  silently ignored.
- Config files are plain YAML mappings. There is no environment-variable substitution; every value
  must come from the file itself or from CLI flags (see [§6](#6-cli-interaction-and-precedence)).

## 2. Quick start

```yaml
# minimal.yaml
ontology:
  displayName: MyOntology

defaults:
  emitUnboundEntities: false
```

```bash
rdf2ontology build --input my_ontology.ttl --config minimal.yaml
```

## 3. File layout convention

The repo's own [defaults.yaml](defaults.yaml) demonstrates the recommended split:

- **One generic file** (e.g. `defaults.yaml`) — RDF-agnostic: only `rdf`, `defaults`, `lint`. No
  entity names, lakehouse ids, or `displayName`. Reused unmodified across every ontology.
- **One per-ontology overrides file** (e.g. `<name>.overrides.yaml`) — everything specific to a
  single ontology: `ontology`, `fabric`, `entities`, `relationships`, `overrides`, `exclude`,
  `customAttributes`.

Layer them together (see next section) so most ontologies only need a small overrides file.

## 4. Layering multiple config files

`--config` is **repeatable** on `lint`, `build`, and `validate` (not on `diff` or `deploy`, which
don't accept a `--config` flag at all — `deploy` works off the already-emitted item tree and
`parameter.yml`, and `diff` compares two emitted item folders).

```bash
rdf2ontology build --input ontology.ttl \
  --config config/defaults.yaml \
  --config config/my_ontology.overrides.yaml
```

Any number of files may be given. They are merged **in argument order**, and validation runs once
against the final merged result — so a key doesn't need to appear in every file, only somewhere in
the combined set.

### Merge algorithm

For each key, comparing the earlier merged value to the next file's value:

| Earlier value | New value | Result |
|---|---|---|
| dict | dict | recursively merged, key by key |
| list | list | concatenated, then **de-duplicated** (order-preserving) |
| anything else, or a type mismatch | any | new value **replaces** the old one |

`config.path` (used for relative-path resolution elsewhere in the tool) is set to the **last** file
in the list.

### Worked example

`a.yaml`:
```yaml
defaults:
  emitUnboundEntities: true
lint:
  ignore: [L-NAME-LENGTH]
```

`b.yaml`:
```yaml
defaults:
  emitUnboundEntities: false
  maxPortalNameLength: 30
lint:
  ignore: [L-SRC-TABLE-FORMAT]
```

`--config a.yaml --config b.yaml` merges to:
```yaml
defaults:
  emitUnboundEntities: false   # scalar: b.yaml wins
  maxPortalNameLength: 30      # only present in b.yaml
lint:
  ignore: [L-NAME-LENGTH, L-SRC-TABLE-FORMAT]   # lists concatenated + deduped
```

Reversing the order (`--config b.yaml --config a.yaml`) would instead leave
`emitUnboundEntities: true`, since `a.yaml` is now the later file.

## 5. Reference: root sections

Unless noted, all sections are optional and default to empty/absent.

### 5.1 `ontology`

| Key | Type | Default | Effect |
|---|---|---|---|
| `displayName` | string | RDF title / input filename | Ontology item name shown in Fabric; also the `build --name` fallback. |
| `logicalId` | string | none | Stable logical id carried into the emitted item. |

### 5.2 `rdf`

Names of the RDF annotation properties the tool looks for on classes/properties.

| Key | Type | Default |
|---|---|---|
| `annotationNamespace` | string | none |
| `sourceTableProperty` | string | `sourceTable` |
| `sourceColumnProperty` | string | `sourceColumn` |
| `sourceLakehouseProperty` | string | `sourceLakehouse` |
| `keyProperty` | string | `isKey` |
| `keyValue` | string | `"true"` |
| `joinConditionProperty` | string | none |
| `synonymsProperty` | string | `synonyms` |

### 5.3 `fabric`

| Key | Type | Default | Effect |
|---|---|---|---|
| `workspaceId` | GUID string | placeholder GUID `00000000-0000-0000-0000-000000000000` | Written into data bindings; a placeholder left in the emitted output triggers validation warning `W7`. |
| `lakehouses` | map of name → lakehouse | `{}` | Named lakehouse bindings, see below. |
| `environments` | freeform map | `{}` | Drives `parameter.yml` generation for environment-specific find/replace of workspace and lakehouse ids. |

`fabric.lakehouses.<name>`:

| Key | Type | Default |
|---|---|---|
| `itemId` | GUID string | placeholder GUID |
| `defaultSchema` | string | `dbo` |
| `workspaceId` | GUID string | none (falls back to `fabric.workspaceId`) |

If exactly one lakehouse is declared, entities that don't name one explicitly resolve to it
automatically.

### 5.4 `defaults`

Tool-wide behavior flags.

| Key | Type | Default | Effect |
|---|---|---|---|
| `emitUnboundEntities` | bool | `true` | Emit reference classes that have no source table binding. |
| `emitUnboundRelationships` | bool | `true` | Emit relationship types that have no contextualization. |
| `emitInverseRelationships` | bool | `false` | Also emit the inverse direction of each relationship. |
| `emitForeignKeyProperties` | bool | `true` | Keep an object property's source column as a queryable scalar property too. |
| `maxPortalNameLength` | int | `26` | Threshold for lint rule `L-NAME-LENGTH`. |
| `unmappedRangeValueType` | string | `String` | Value type used when a property's range can't be mapped to a known type. |
| `autoPrefixTimeseries` | bool | `true` | Auto-prefix generated timeseries property names to avoid collisions. |

### 5.5 `lint`

| Key | Type | Default | Effect |
|---|---|---|---|
| `failOn` | `error` \| `warning` | `error` | Any other value raises `ConfigError`. |
| `ignore` | list of rule ids | `[]` | Merged with any `--ignore` CLI flags. |

### 5.6 `overrides`

| Key | Type | Effect |
|---|---|---|
| `valueTypes` | map of `"Entity.Prop"` or bare `"Prop"` → value type | Looked up entity-scoped first, then bare property name as fallback. |
| `rename` | map of original name → new name | Applied to entity/property/relationship names during mapping. |

### 5.7 `exclude`

| Key | Type | Effect |
|---|---|---|
| `classes` | list of class names | Excludes the whole entity type. Equivalent to setting `entities.<Name>.exclude: true`. |
| `properties` | list of `"Entity.Prop"` or bare `"Prop"` | Excludes a property; bare names apply everywhere. |

### 5.8 `customAttributes`

Projects extra RDF annotations into each emitted object's `semanticEnrichment.customAttributes`.

| Key | Type | Default |
|---|---|---|
| `entities` | list of annotation local names (or `label`/`domain`/`range`) | `[]` |
| `dataProperties` | list of annotation local names | `[]` |
| `objectProperties` | list of annotation local names | `[]` |

### 5.9 `entities`

Freeform map keyed by RDF class name.

| Key | Type | Effect |
|---|---|---|
| `key` | string or list of strings | Explicit key property/properties (overrides `rdf.keyProperty` annotation lookup). |
| `displayNameProperty` | string | Property used as the entity's display name. |
| `sourceTable` | string | Overrides the `sourceTable` RDF annotation. |
| `sourceLakehouse` | string | Name of an entry in `fabric.lakehouses`. |
| `timeseries` | list of timeseries bindings, see below | Adds timeseries data bindings for this entity. |
| `exclude` | bool | Excludes this entity, same as listing it in `exclude.classes`. |

`entities.<Name>.timeseries[]` items — `table` and `timestampColumn` are **required**, all others
optional:

| Key | Required | Type | Effect |
|---|---|---|---|
| `table` | yes | string | Source table for the timeseries data. |
| `timestampColumn` | yes | string | Column used as the timeseries index. |
| `properties` | no | list of strings | Columns to bind as timeseries properties. |
| `lakehouse` | no | string | Name of an entry in `fabric.lakehouses`; defaults like `sourceLakehouse`. |

Missing `table` or `timestampColumn` raises `ConfigError`.

### 5.10 `relationships`

Freeform map keyed by relationship name.

| Key | Type | Effect |
|---|---|---|
| `name` | string | Renames the relationship in the emitted output. |
| `linkTable` | string | Join table backing the relationship. |
| `sourceKeyColumns` | list of strings | Key columns on the source entity's table. |
| `targetKeyColumns` | list of strings | Key columns on the target entity's table. |
| `exclude` | bool | Excludes this relationship. |

## 6. CLI interaction and precedence

`build`/`lint`/`validate` all accept:

- `--config PATH` (repeatable, see [§4](#4-layering-multiple-config-files))
- `--strict` — promote warnings to errors
- `--ignore RULE` (repeatable) — merged with `lint.ignore` from config

`build` additionally accepts:

- `--workspace-id GUID` — **only applied if the config still has the placeholder GUID**; a
  `fabric.workspaceId` set in config always wins over this flag.
- `--lakehouse-id [NAME=]GUID` (repeatable) — a bare GUID is a fallback used for any unresolved
  lakehouse; `NAME=GUID` scopes it to one named lakehouse. Only fills lakehouses whose `itemId` in
  config is still the placeholder GUID.
- `--schema NAME` (default `dbo`) — default schema for lakehouse tables with no schema prefix.

In short: **config file values always win over CLI flags** — CLI flags only fill in placeholders
left unresolved by the config.

## 7. Examples

### E1 — Bare minimum

```yaml
ontology:
  displayName: MyOntology
```

### E2 — Remapping the RDF annotation vocabulary

```yaml
rdf:
  sourceTableProperty: physicalTableName
  sourceColumnProperty: physicalColumnName
  keyProperty: classification
  keyValue: Key
  joinConditionProperty: joinCondition
```

### E3 — Single lakehouse binding + schema

```yaml
fabric:
  workspaceId: 11111111-1111-1111-1111-111111111111
  lakehouses:
    default:
      itemId: 22222222-2222-2222-2222-222222222222
      defaultSchema: bronze
```

### E4 — Multiple named lakehouses

```yaml
fabric:
  lakehouses:
    bronze:
      itemId: 33333333-3333-3333-3333-333333333333
      defaultSchema: bronze
    curated:
      itemId: 44444444-4444-4444-4444-444444444444
      defaultSchema: gold
```

### E5 — Explicit entity key and source table

```yaml
entities:
  Customer:
    sourceTable: dim_customer
    key: customerId          # single key column
  OrderLine:
    key: [orderId, lineNo]   # composite key
```

### E6 — Timeseries binding

```yaml
entities:
  Meter:
    timeseries:
      - table: fact_meter_reading
        timestampColumn: readingTimestamp
        properties: [readingValue, readingQuality]
        lakehouse: curated
```

### E7 — Relationship override

```yaml
relationships:
  Customer_HasMainAddress_Address:
    name: hasMainAddress
    linkTable: bridge_customer_address
    sourceKeyColumns: [customerId]
    targetKeyColumns: [addressId]
```

### E8 — Value type overrides and renames

```yaml
overrides:
  valueTypes:
    Invoice.dueDate: String     # entity-scoped, checked first
    createdAt: String           # bare name, applies wherever no entity-scoped entry matches
  rename:
    Addr: Address
```

### E9 — Exclusions

```yaml
exclude:
  classes: [EnumerationValue, BusinessTerm]
  properties: [internalNotes]

entities:
  DeprecatedWidget:
    exclude: true   # equivalent to listing it in exclude.classes
```

### E10 — Custom attributes projection

```yaml
customAttributes:
  entities: [synonyms]
  dataProperties: [label, domain]
  objectProperties: [range]
```

### E11 — Environments for `parameter.yml`

```yaml
fabric:
  environments:
    dev:
      workspaceId: 55555555-5555-5555-5555-555555555555
    prod:
      workspaceId: 66666666-6666-6666-6666-666666666666
```

### E12 — Lint tuning

```yaml
lint:
  failOn: warning
  ignore: [L-NAME-LENGTH, L-SRC-TABLE-FORMAT]
```

### E13 — Layered pair (generic + per-ontology overrides)

`config/defaults.yaml` (generic, reused everywhere):
```yaml
defaults:
  emitUnboundEntities: true
lint:
  failOn: error
```

`config/widgets.overrides.yaml` (per-ontology):
```yaml
ontology:
  displayName: Widgets
defaults:
  emitUnboundEntities: false
```

```bash
rdf2ontology build --input widgets.ttl \
  --config config/defaults.yaml \
  --config config/widgets.overrides.yaml
```

Merged result: `ontology.displayName: Widgets`, `defaults.emitUnboundEntities: false` (overrides.yaml
wins on the scalar), `lint.failOn: error` (only present in defaults.yaml).

## 8. Validation, errors, and exit codes

| Situation | Error |
|---|---|
| Unrecognized key anywhere | `ConfigError: unknown key(s) in '<section>': ... Allowed: ...` |
| `lint.failOn` not `error`/`warning` | `ConfigError: lint.failOn must be 'error' or 'warning'` |
| `--config` path doesn't exist | `ConfigError: config file not found: <path>` |
| Config file's YAML root isn't a mapping | `ConfigError: config root must be a mapping: <path>` |
| `entities.<Name>.timeseries[i]` missing `table` or `timestampColumn` | `ConfigError: entities.<Name>.timeseries[i] is missing '<field>'` |

CLI exit codes: `0` OK, `1` validation failure, `2` bad input, `3` deploy failure, `4` lint failure.

## 9. Troubleshooting

- **Placeholder GUID warning (`W7`)** — `fabric.workspaceId` (or a lakehouse `itemId`) is still
  `00000000-0000-0000-0000-000000000000`. Set it in config or pass `--workspace-id`/`--lakehouse-id`.
- **`L-SRC-LAKEHOUSE` lint rule** only fires once `fabric.lakehouses` names at least one lakehouse —
  it's silent for RDF-only runs with no config.
- **`E15` unknown entity reference** — a relationship or property points at a class that was
  excluded or never mapped; check `exclude.classes` and `emitUnboundEntities`.
- **List values "growing" unexpectedly across layered files** — `lint.ignore`, `exclude.classes`,
  etc. concatenate and de-duplicate rather than replace; put the full desired list in the
  last-loaded file if you need to fully replace one.
- **A CLI flag seems to have no effect** — config always wins over `--workspace-id`/`--lakehouse-id`
  when config already sets a non-placeholder value.
