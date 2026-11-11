from typing import Optional


class RecoverySession:
    """Remembers, for one analysis, whether the engineers were already alerted and which
    backup model they approved, so every AI call of that analysis shares the same answer."""

    def __init__(self) -> None:
        self._notified = False
        self._manual_model: Optional[str] = None

    @property
    def manual_model(self) -> Optional[str]:
        return self._manual_model

    @manual_model.setter
    def manual_model(self, model: str) -> None:
        """Record the backup model the engineers approved; a blank name is refused."""
        if not model or not str(model).strip():
            raise ValueError("manual_model must not be empty")
        self._manual_model = str(model).strip()

    def was_notified(self) -> bool:
        return self._notified

    def mark_notified(self) -> None:
        self._notified = True
