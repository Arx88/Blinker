from typing import List, Dict, Any, Optional
from pydantic import BaseModel

class McpConfig(BaseModel):
    qualifiedName: str
    name: str
    config: Dict[str, Any]
    enabledTools: Optional[List[str]] = None
