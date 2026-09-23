from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any], dict[str, Any]], Any]
    access: str = "read"
    risk: str = "safe"
    requires_confirmation: bool = False
    category: str = "general"

    def manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "access": self.access,
            "risk": self.risk,
            "requires_confirmation": self.requires_confirmation,
            "category": self.category,
            "parameters": self.parameters,
        }


class ToolRegistry:
    """Registro controllato di funzioni invocabili dall'assistente.

    Ogni tool riceve ``context`` e ``arguments``. Il registry impedisce
    l'esecuzione di funzioni non registrate e applica il livello di accesso.

    Accessi:
      - read: legge/analizza lo stato corrente;
      - generate: produce una proposta, un report o un Change Set senza mutare dati;
      - write: modifica lo stato e deve essere invocato dal codice applicativo dopo
        una conferma esplicita, non direttamente dal planner LLM.
    """

    VALID_ACCESS = {"read", "generate", "write"}
    VALID_RISK = {"safe", "controlled", "sensitive"}

    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if not spec.name or not spec.name.replace("_", "").isalnum():
            raise ValueError(f"Nome tool non valido: {spec.name!r}")
        if spec.name in self._tools:
            raise ValueError(f"Tool già registrato: {spec.name}")
        if spec.access not in self.VALID_ACCESS:
            raise ValueError(f"Livello accesso non valido: {spec.access}")
        if spec.risk not in self.VALID_RISK:
            raise ValueError(f"Livello rischio non valido: {spec.risk}")
        if spec.access == "write" and not spec.requires_confirmation:
            raise ValueError(
                f"Il tool WRITE {spec.name} deve richiedere conferma esplicita."
            )
        self._tools[spec.name] = spec

    def manifest(self, access: set[str] | None = None) -> list[dict[str, Any]]:
        tools = list(self._tools.values())
        if access is not None:
            tools = [tool for tool in tools if tool.access in access]
        return [tool.manifest() for tool in tools]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"Tool non registrato: {name}")
        return self._tools[name]

    def execute(
        self,
        name: str,
        context: dict[str, Any],
        arguments: dict[str, Any] | None = None,
        *,
        allowed_access: set[str] | None = None,
        confirmed: bool = False,
    ) -> Any:
        spec = self.get(name)
        if allowed_access is not None and spec.access not in allowed_access:
            raise PermissionError(
                f"Il tool {name} richiede accesso '{spec.access}', non consentito in questo contesto."
            )
        if spec.requires_confirmation and not confirmed:
            raise PermissionError(
                f"Il tool {name} richiede conferma esplicita prima dell'esecuzione."
            )
        return spec.handler(context, dict(arguments or {}))
