"""rdf2ontology - RDF/OWL to Fabric IQ Ontology definition transformer."""

__version__ = "0.1.0"

from .config import Config, ConfigError, load_config
from .diagnostics import Diagnostic, DiagnosticBag, Severity
from .ids import IdMap
from .ir import EntityIR, OntologyIR, PropertyIR, RelationshipIR

__all__ = [
    "__version__",
    "Config",
    "ConfigError",
    "load_config",
    "Diagnostic",
    "DiagnosticBag",
    "Severity",
    "IdMap",
    "EntityIR",
    "OntologyIR",
    "PropertyIR",
    "RelationshipIR",
]
