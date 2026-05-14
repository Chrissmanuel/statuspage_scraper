from dataclasses import dataclass
from typing import Optional, Dict, Any

@dataclass(frozen=True)
class SelectorMap:
    titulo: str
    periodo: str
    duracion_raw: str
    resumen: str
    estado: Optional[str] = None

@dataclass(frozen=True)
class ProveedorConfig:
    nombre: str
    url: str
    container: str
    selectores: SelectorMap
    tipo: str
    navegacion_profunda: bool = False

@dataclass
class IncidentData:
    Proveedor: str
    Titulo: str
    Periodo: str
    Resumen: str
    Estado: str = "N/A"
    Componentes: str = "N/A"
    Duracion_Minutos: int = 0
    Pendiente: str = "NO"
    ID: str = ""
    Asignado: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "Proveedor": self.Proveedor,
            "Titulo": self.Titulo,
            "Periodo": self.Periodo,
            "Resumen": self.Resumen,
            "Estado": self.Estado,
            "Componentes": self.Componentes,
            "Duracion_Minutos": str(self.Duracion_Minutos),
            "Pendiente": self.Pendiente,
            "ID": self.ID,
            "Asignado": self.Asignado,
        }