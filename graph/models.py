from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class AssetType(str, Enum):

    DATABASE = "database"

    TABLE = "table"

    COLUMN = "column"

    VIEW = "view"

    STORED_PROCEDURE = "stored_procedure"

    FUNCTION = "function"

    NOTEBOOK = "notebook"

    DELTA_TABLE = "delta_table"

    PIPELINE = "pipeline"

    JOB = "job"

    API = "api"

    SEMANTIC_MODEL = "semantic_model"

    REPORT = "report"

    VISUAL = "visual"

    MEASURE = "measure"

    DASHBOARD = "dashboard"


class SystemType(str, Enum):

    DATABASE = "database"

    SQL = "sql"

    DATABRICKS = "databricks"

    PIPELINE = "pipeline"

    POWERBI = "powerbi"

    API = "api"


class RelationshipType(str, Enum):

    USES = "USES"

    READS = "READS"

    WRITES = "WRITES"

    FEEDS = "FEEDS"

    CALLS = "CALLS"

    REFERENCES = "REFERENCES"

    DISPLAYS = "DISPLAYS"

    DEPENDS_ON = "DEPENDS_ON"


@dataclass
class Asset:

    id: str

    name: str

    asset_type: AssetType

    system: SystemType

    properties: Dict = field(default_factory=dict)


@dataclass
class Relationship:

    source: str

    target: str

    relationship: RelationshipType

    properties: Dict = field(default_factory=dict)